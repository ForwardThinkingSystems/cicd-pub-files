import os
import requests
import json
import base64
from typing import Dict, List, Optional

DEFAULT_PRE_PROMPT = """
As a code reviewer, please analyze the changes with the following priorities:

1. Code Quality & Best Practices:
   - Clean code principles (DRY, SOLID, KISS)
   - Proper error handling and logging
   - Appropriate use of comments and documentation
   - Consistent naming conventions and formatting

2. Security:
   - Input validation and sanitization
   - Authentication and authorization checks
   - Secure data handling and storage
   - Prevention of common vulnerabilities (XSS, CSRF, SQL injection)

3. Performance:
   - Algorithmic efficiency
   - Resource usage (memory, CPU)
   - Database query optimization
   - Caching considerations

4. Testing:
   - Test coverage for new code
   - Edge cases consideration
   - Integration test requirements
   - Mocking strategy where applicable

5. Maintainability:
   - Code complexity
   - Module coupling and cohesion
   - Future scalability implications
   - Technical debt assessment

Please be specific in your feedback and provide actionable suggestions with code examples where appropriate.
Highlight both areas of concern and instances of good practices.
"""

class ClaudePRReviewer:
    def __init__(self):
        self.claude_api_key = os.getenv('CLAUDE_API_KEY')
        self.bitbucket_username = os.getenv('BITBUCKET_USERNAME')
        self.bitbucket_token = os.getenv('BITBUCKET_TOKEN')
        self.pre_prompt_text = os.getenv('PRE_PROMPT_TEXT', DEFAULT_PRE_PROMPT)
        self.workspace = os.getenv('BITBUCKET_WORKSPACE')
        self.repo_slug = os.getenv('BITBUCKET_REPO_SLUG')
        self.pr_id = os.getenv('BITBUCKET_PR_ID')
        
        # Validate required environment variables
        required_vars = {
            'CLAUDE_API_KEY': self.claude_api_key,
            'BITBUCKET_USERNAME': self.bitbucket_username,
            'BITBUCKET_TOKEN': self.bitbucket_token,
            'BITBUCKET_WORKSPACE': self.workspace,
            'BITBUCKET_REPO_SLUG': self.repo_slug,
            'BITBUCKET_PR_ID': self.pr_id
        }
        
        missing_vars = [var for var, value in required_vars.items() if not value]
        if missing_vars:
            raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")
        
        # Create auth string for basic auth
        auth_str = f"{self.bitbucket_username}:{self.bitbucket_token}"
        auth_bytes = auth_str.encode('ascii')
        self.auth_header = base64.b64encode(auth_bytes).decode('ascii')
        
        self.headers = {
            'Authorization': f'Basic {self.auth_header}',
            'Accept': 'application/json'
        }
        
        self.bb_api_base = f"https://api.bitbucket.org/2.0/repositories/{self.workspace}/{self.repo_slug}"
        
    def test_auth(self) -> bool:
        """Test authentication with Bitbucket API"""
        try:
            test_url = f"https://api.bitbucket.org/2.0/repositories/{self.workspace}"
            response = requests.get(test_url, headers=self.headers)
            if response.status_code != 200:
                print(f"Authentication test failed with status {response.status_code}")
                print("Response:", response.text)
                return False
            return True
        except requests.exceptions.RequestException as e:
            print(f"Authentication test failed with error: {e}")
            return False
        
    def get_pr_changes(self) -> Dict:
        """Fetch the PR diff and changed files."""
        print(f"Testing Bitbucket API authentication...")
        if not self.test_auth():
            raise Exception("Failed to authenticate with Bitbucket API")
            
        print(f"Making API call to: {self.bb_api_base}/pullrequests/{self.pr_id}/diff")
        
        try:
            # Get the diff
            diff_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}/diff"
            diff_response = requests.get(diff_url, headers=self.headers)
            if diff_response.status_code == 401:
                print("Authentication failed. Response:", diff_response.text)
                raise Exception("Authentication failed with Bitbucket API")
            diff_response.raise_for_status()
            
            # Get the list of changed files
            files_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}/diffstat"
            files_response = requests.get(files_url, headers=self.headers)
            files_response.raise_for_status()
            
            # Get PR description for additional context
            pr_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}"
            pr_response = requests.get(pr_url, headers=self.headers)
            pr_response.raise_for_status()
            
            return {
                "diff": diff_response.text,
                "changed_files": files_response.json()["values"],
                "pr_info": pr_response.json()
            }
        except requests.exceptions.RequestException as e:
            print(f"Failed to fetch PR changes: {e}")
            raise
        
    def analyze_with_claude(self, changes: Dict) -> Dict:
        """Send the code changes to Claude for analysis."""
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": self.claude_api_key
        }
        
        pr_description = changes['pr_info'].get('description', 'No description provided')
        pr_title = changes['pr_info'].get('title', 'Untitled PR')
        
        # Construct the messages
        messages = [
            {
                "role": "system",
                "content": "You are a code review assistant. Analyze code changes and provide detailed feedback in JSON format."
            },
            {
                "role": "user",
                "content": f"""
{self.pre_prompt_text}

Pull Request Information:
Title: {pr_title}
Description: {pr_description}

Changed Files:
{json.dumps([f['new']['path'] for f in changes['changed_files']], indent=2)}

Diff Content:
{changes['diff']}

Please analyze these changes and provide a detailed review following the guidelines above.
Format your response as JSON with the following structure:
{{
    "summary": "Overall review summary",
    "issues": [
        {{
            "file": "filename",
            "line": line_number,
            "severity": "high|medium|low",
            "category": "security|performance|quality|testing|maintainability",
            "description": "Issue description",
            "suggestion": "How to fix",
            "good_practice": boolean
        }}
    ],
    "recommendations": ["List of general recommendations"],
    "positive_notes": ["List of good practices identified"]
}}"""
            }
        ]

        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json={
                    "model": "claude-3-sonnet-20240229",
                    "max_tokens": 4096,
                    "messages": messages,
                    "temperature": 0.7
                }
            )
            
            if response.status_code != 200:
                print(f"Claude API Error: {response.status_code}")
                print(f"Response: {response.text}")
                raise Exception(f"Claude API returned status code {response.status_code}")
            
            response_data = response.json()
            if "content" not in response_data or not response_data["content"]:
                raise Exception("No content in Claude's response")
                
            review_content = response_data["content"][0]["text"]
            return json.loads(review_content)
            
        except requests.exceptions.RequestException as e:
            print(f"Request failed: {e}")
            raise
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Failed to parse response: {e}")
            print(f"Response content: {response.text if 'response' in locals() else 'No response'}")
            raise Exception(f"Failed to parse Claude's response: {e}")
        
    def post_comments(self, review: Dict) -> None:
        """Post the review comments to the PR."""
        try:
            # Create a detailed summary comment
            summary_markdown = f"""# Claude Code Review Summary

{review['summary']}

## 🎯 General Recommendations
{chr(10).join(f"- {rec}" for rec in review['recommendations'])}

## ✨ Positive Notes
{chr(10).join(f"- {note}" for note in review.get('positive_notes', []))}

## 📊 Issues Overview
- High Severity: {sum(1 for i in review['issues'] if i['severity'] == 'high')}
- Medium Severity: {sum(1 for i in review['issues'] if i['severity'] == 'medium')}
- Low Severity: {sum(1 for i in review['issues'] if i['severity'] == 'low')}
"""
            
            # Post summary comment
            comments_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}/comments"
            response = requests.post(
                comments_url, 
                headers=self.headers, 
                json={"content": {"raw": summary_markdown}}
            )
            response.raise_for_status()
            
            # Post individual issue comments
            for issue in review['issues']:
                severity_emoji = {
                    'high': '🔴',
                    'medium': '🟡',
                    'low': '🟢'
                }.get(issue['severity'], '⚪️')
                
                comment = {
                    "content": {
                        "raw": f"""**{severity_emoji} {issue['severity'].upper()} Severity {issue['category'].title()} Issue**

{issue['description']}

**Suggestion:** {issue['suggestion']}

{f"✨ **Good Practice!**" if issue.get('good_practice', False) else ""}"""
                    },
                    "inline": {
                        "path": issue['file'],
                        "to": issue['line']
                    }
                }
                response = requests.post(comments_url, headers=self.headers, json=comment)
                response.raise_for_status()
                
        except requests.exceptions.RequestException as e:
            print(f"Failed to post comments: {e}")
            raise

    def run_review(self) -> bool:
        """Execute the complete review process."""
        try:
            print("🔍 Fetching PR changes...")
            changes = self.get_pr_changes()
            
            print("📝 Analyzing changes with Claude...")
            review = self.analyze_with_claude(changes)
            
            print("💬 Posting review comments...")
            self.post_comments(review)
            
            # Count high and medium severity issues
            high_severity_count = sum(1 for i in review['issues'] if i['severity'] == 'high')
            medium_severity_count = sum(1 for i in review['issues'] if i['severity'] == 'medium')
            
            # Fail if there are any high severity issues or more than 3 medium severity issues
            should_fail = high_severity_count > 0 or medium_severity_count > 3
            
            print(f"""
Review completed:
- High severity issues: {high_severity_count}
- Medium severity issues: {medium_severity_count}
- Pipeline status: {"❌ Failed" if should_fail else "✅ Passed"}
""")
            
            return not should_fail
            
        except Exception as e:
            print(f"❌ Error during review process: {e}")
            return False

if __name__ == "__main__":
    reviewer = ClaudePRReviewer()
    success = reviewer.run_review()
    exit(0 if success else 1)

#!/usr/bin/env python3
import os
import requests
import json
import base64
from typing import Dict, List, Optional
from anthropic import Anthropic

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
        # Required environment variables
        self.claude_api_key = os.getenv('CLAUDE_API_KEY')
        self.bitbucket_username = os.getenv('BITBUCKET_USERNAME')
        self.bitbucket_token = os.getenv('BITBUCKET_TOKEN')  # App password
        self.workspace = os.getenv('BITBUCKET_WORKSPACE')
        self.repo_slug = os.getenv('BITBUCKET_REPO_SLUG')
        self.pr_id = os.getenv('BITBUCKET_PR_ID')
        
        # Optional environment variables with defaults
        self.pre_prompt_text = os.getenv('PRE_PROMPT_TEXT', DEFAULT_PRE_PROMPT)
        include_low_severity = os.getenv('INCLUDE_LOW_SEVERITY')
        if include_low_severity is None:
            print("ℹ️ INCLUDE_LOW_SEVERITY not set, defaulting to false")
            self.include_low_severity = False
        else:
            self.include_low_severity = include_low_severity.lower() == 'true'
            print(f"ℹ️ INCLUDE_LOW_SEVERITY set to: {self.include_low_severity}")
        
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
        
        # Initialize Anthropic client
        self.client = Anthropic(api_key=self.claude_api_key)
        
        # Setup Bitbucket authentication
        auth_str = f"{self.bitbucket_username}:{self.bitbucket_token}"
        self.auth_header = base64.b64encode(auth_str.encode('ascii')).decode('ascii')
        
        self.headers = {
            'Authorization': f'Basic {self.auth_header}',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        }
        
        self.bb_api_base = f"https://api.bitbucket.org/2.0/repositories/{self.workspace}/{self.repo_slug}"
        
        # Print configuration
        print("\nConfiguration Summary:")
        print(f"- Workspace: {self.workspace}")
        print(f"- Repository: {self.repo_slug}")
        print(f"- PR ID: {self.pr_id}")
        print(f"- Include Low Severity Issues: {self.include_low_severity}")
        print(f"- Using Custom Pre-Prompt: {'Yes' if self.pre_prompt_text != DEFAULT_PRE_PROMPT else 'No'}\n")
    
    def test_auth(self) -> bool:
        """Test authentication with Bitbucket API"""
        try:
            test_url = f"{self.bb_api_base}/pullrequests"
            print(f"Testing Bitbucket API access to: {test_url}")
            response = requests.get(test_url, headers=self.headers)
            
            if response.status_code == 200:
                print("✅ Bitbucket API authentication successful")
                return True
            
            print(f"❌ Authentication test failed with status {response.status_code}")
            print("Response:", response.text)
            return False
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Authentication test failed with error: {e}")
            return False
    
    def check_existing_reviews(self) -> bool:
        """Check if a review comment already exists for the PR."""
        try:
            comments_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}/comments"
            response = requests.get(comments_url, headers=self.headers)
            response.raise_for_status()

            comments = response.json().get("values", [])
            for comment in comments:
                if "Claude Code Review Summary" in comment["content"]["raw"]:
                    print("🔍 Found previous review comment, skipping review process.")
                    return True
            return False
        except Exception as e:
            print(f"Warning: Failed to check existing reviews: {e}")
            return False

    def get_pr_changes(self) -> Dict:
        """Fetch the PR diff and changed files."""
        if not self.test_auth():
            raise Exception("Failed to authenticate with Bitbucket API")
        
        try:
            # Get the diff
            diff_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}/diff"
            print(f"Fetching diff from: {diff_url}")
            diff_response = requests.get(diff_url, headers=self.headers)
            diff_response.raise_for_status()
            
            # Get the list of changed files
            files_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}/diffstat"
            print(f"Fetching changed files from: {files_url}")
            files_response = requests.get(files_url, headers=self.headers)
            files_response.raise_for_status()
            
            # Get PR description for additional context
            pr_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}"
            print(f"Fetching PR details from: {pr_url}")
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
        pr_description = changes['pr_info'].get('description', 'No description provided')
        pr_title = changes['pr_info'].get('title', 'Untitled PR')
        
        user_message = f"""
{self.pre_prompt_text}

Title: {pr_title}
Description: {pr_description}

Changed Files:
{json.dumps([f['new']['path'] for f in changes['changed_files']], indent=2)}

Diff Content:
{changes['diff']}

Please analyze these changes and provide a detailed review following the guidelines above.
Format your response as a valid JSON object with this structure:
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
}}

Important: Respond with ONLY the JSON object, no additional text, markdown, or code blocks."""
        
        try:
            message = self.client.messages.create(
                model="claude-3-sonnet-20240229",
                max_tokens=4096,
                system="You are a code review assistant. Return only a valid JSON object without any markdown, explanations, or code blocks.",
                messages=[{"role": "user", "content": user_message}]
            )
            
            # Get response text and clean it
            response_text = message.content[0].text.strip()
            
            # Remove any markdown code block markers if present
            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.startswith("```"):
                response_text = response_text[3:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            
            response_text = response_text.strip()
            
            # Try to parse the JSON
            try:
                review_data = json.loads(response_text)
                return review_data
            except json.JSONDecodeError as e:
                print(f"Failed to parse Claude's response as JSON: {e}")
                print("Response content:")
                print(response_text)
                raise
                
        except Exception as e:
            print(f"Error during Claude API call: {e}")
            raise

    def post_comments(self, review: Dict) -> None:
        """Post the review comments to the PR."""
        try:
            # Filter issues based on severity
            issues_to_include = [
                issue for issue in review['issues']
                if self.include_low_severity or issue['severity'] != 'low'
            ]
            
            # Update issues count for summary
            high_count = sum(1 for i in issues_to_include if i['severity'] == 'high')
            medium_count = sum(1 for i in issues_to_include if i['severity'] == 'medium')
            low_count = sum(1 for i in issues_to_include if i['severity'] == 'low')
            
            # Create summary comment
            summary_markdown = f"""# Claude Code Review Summary

{review['summary']}

## 🎯 General Recommendations
{chr(10).join(f"- {rec}" for rec in review['recommendations'])}

## ✨ Positive Notes
{chr(10).join(f"- {note}" for note in review.get('positive_notes', []))}

## 📊 Issues Overview
- High Severity: {high_count}
- Medium Severity: {medium_count}
- Low Severity: {low_count}
{"- Note: Low severity issues are hidden (set INCLUDE_LOW_SEVERITY=true to show them)" if not self.include_low_severity and low_count > 0 else ""}
"""
            
            # Post summary comment
            comments_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}/comments"
            print(f"Posting summary comment to: {comments_url}")
            
            response = requests.post(
                comments_url, 
                headers=self.headers, 
                json={"content": {"raw": summary_markdown}}
            )
            response.raise_for_status()
            
            # Post individual issue comments
            print("Posting individual issue comments...")
            for issue in issues_to_include:
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
                    }
                }
                
                # Add inline comment data if file and line are present
                if issue.get('file') and issue.get('line'):
                    comment['inline'] = {
                        "path": issue['file'],
                        "to": issue['line']
                    }
                
                response = requests.post(comments_url, headers=self.headers, json=comment)
                response.raise_for_status()
                print(f"Posted comment for {issue['severity']} severity issue in {issue.get('file', 'general comment')}")
                
        except requests.exceptions.RequestException as e:
            print(f"Failed to post comments: {e}")
            if hasattr(e.response, 'text'):
                print(f"Response: {e.response.text}")
            raise

    def run_review(self) -> bool:
        """Execute the complete review process."""
        try:
            if self.check_existing_reviews():
                return True
                
            print("\n🔍 Fetching PR changes...")
            changes = self.get_pr_changes()
            
            print("\n📝 Analyzing changes with Claude...")
            review = self.analyze_with_claude(changes)
            
            print("\n💬 Posting review comments...")
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
- Low severity issues: {sum(1 for i in review['issues'] if i['severity'] == 'low')}
  {'(hidden from PR comments)' if not self.include_low_severity else '(included in PR comments)'}
- Pipeline status: {"❌ Failed" if should_fail else "✅ Passed"}
""")
            
            return not should_fail
            
        except Exception as e:
            print(f"\n❌ Error during review process: {e}")
            return False

if __name__ == "__main__":
    reviewer = ClaudePRReviewer()
    success = reviewer.run_review()
    exit(0 if success else 1)

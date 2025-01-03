import os
import requests
import json
import base64
from typing import Dict, List, Optional
from anthropic import Anthropic

DEFAULT_PRE_PROMPT = """
Analyze the following code changes and provide a concise review focusing on:

1. **Security:** Common vulnerabilities like XSS, CSRF, SQL injection.
2. **Code Quality:** Clean code principles, error handling, and documentation.
3. **Performance:** Algorithm efficiency and resource usage.
4. **Testing:** Adequate test coverage and edge case handling.
5. **Maintainability:** Code complexity and future scalability.

Provide actionable suggestions with examples where necessary. Highlight both concerns and good practices.
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
        self.include_low_severity = os.getenv('INCLUDE_LOW_SEVERITY', 'false').lower() == 'true'
        
        # Initialize Anthropic client
        self.client = Anthropic(api_key=self.claude_api_key)
        
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
        """Test authentication with Bitbucket API using PR scope"""
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
        comments_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}/comments"
        response = requests.get(comments_url, headers=self.headers)
        response.raise_for_status()

        comments = response.json()["values"]
        for comment in comments:
            if "Review completed" in comment["content"]["raw"]:
                print("🔍 Found previous review comment, skipping review process.")
                return True
        return False

    def get_pr_changes(self) -> Dict:
        """Fetch the PR diff and changed files."""
        if not self.test_auth():
            raise Exception("Failed to authenticate with Bitbucket API")
        
        diff_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}/diff"
        files_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}/diffstat"
        pr_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}"
        
        diff_response = requests.get(diff_url, headers=self.headers)
        diff_response.raise_for_status()
        files_response = requests.get(files_url, headers=self.headers)
        files_response.raise_for_status()
        pr_response = requests.get(pr_url, headers=self.headers)
        pr_response.raise_for_status()
        
        return {
            "diff": diff_response.text,
            "changed_files": files_response.json()["values"],
            "pr_info": pr_response.json()
        }
    
    def analyze_with_claude(self, changes: Dict) -> Dict:
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
"""
        
        message = self.client.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=4096,
            system="You are a code review assistant.",
            messages=[{"role": "user", "content": user_message}]
        )
        
        return json.loads(message.content[0].text)

    def run_review(self) -> bool:
        try:
            if self.check_existing_reviews():
                print("✅ Skipping review since it's already completed.")
                return True
            
            changes = self.get_pr_changes()
            review = self.analyze_with_claude(changes)
            
            if not self.include_low_severity:
                review['issues'] = [issue for issue in review['issues'] if issue['severity'] != 'low']
            
            print("🔍 Review completed successfully.")
            return True
        
        except Exception as e:
            print(f"❌ Error during review process: {e}")
            return False

if __name__ == "__main__":
    reviewer = ClaudePRReviewer()
    success = reviewer.run_review()
    exit(0 if success else 1)

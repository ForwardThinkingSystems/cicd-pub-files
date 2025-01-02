# analyze_pr.py
import os
import requests
import json
from typing import Dict, List, Optional

class ClaudePRReviewer:
    def __init__(self):
        self.claude_api_key = os.getenv('CLAUDE_API_KEY')
        self.bitbucket_token = os.getenv('BITBUCKET_TOKEN')
        self.pre_prompt_text = os.getenv('PRE_PROMPT_TEXT')
        self.workspace = os.getenv('BITBUCKET_WORKSPACE')
        self.repo_slug = os.getenv('BITBUCKET_REPO_SLUG')
        self.pr_id = os.getenv('BITBUCKET_PR_ID')
        
        if not all([self.claude_api_key, self.bitbucket_token, self.pre_prompt_text,
                   self.workspace, self.repo_slug, self.pr_id]):
            raise EnvironmentError("Missing required environment variables")
        
        self.bb_api_base = f"https://api.bitbucket.org/2.0/repositories/{self.workspace}/{self.repo_slug}"
        
    def get_pr_changes(self) -> List[Dict]:
        """Fetch the PR diff and changed files."""
        url = f"{self.bb_api_base}/pullrequests/{self.pr_id}/diff"
        headers = {"Authorization": f"Bearer {self.bitbucket_token}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        # Get the list of changed files
        files_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}/diffstat"
        files_response = requests.get(files_url, headers=headers)
        files_response.raise_for_status()
        
        return {
            "diff": response.text,
            "changed_files": files_response.json()["values"]
        }
        
    def analyze_with_claude(self, changes: Dict) -> Dict:
        """Send the code changes to Claude for analysis."""
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.claude_api_key,
            "anthropic-version": "2024-01-01"
        }
        
        prompt = f"""
{self.pre_prompt_text}

Here are the code changes to review:

{changes['diff']}

Please provide a detailed review focusing on:
1. Potential bugs or logical errors
2. Security concerns
3. Performance implications
4. Code style and best practices
5. Suggestions for improvement

Format your response as JSON with the following structure:
{{
    "summary": "Overall review summary",
    "issues": [
        {{
            "file": "filename",
            "line": line_number,
            "severity": "high|medium|low",
            "description": "Issue description",
            "suggestion": "How to fix"
        }}
    ],
    "recommendations": ["List of general recommendations"]
}}
"""

        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json={
                "model": "claude-3-sonnet-20240229",
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}]
            }
        )
        response.raise_for_status()
        
        # Extract the JSON response from Claude's message
        try:
            review_content = response.json()["content"][0]["text"]
            return json.loads(review_content)
        except (json.JSONDecodeError, KeyError) as e:
            raise Exception(f"Failed to parse Claude's response: {e}")
        
    def post_comments(self, review: Dict) -> None:
        """Post the review comments to the PR."""
        headers = {
            "Authorization": f"Bearer {self.bitbucket_token}",
            "Content-Type": "application/json"
        }
        
        # Post overall summary comment
        summary_comment = {
            "content": {
                "raw": f"# Claude Code Review Summary\n\n{review['summary']}\n\n"
                       f"## General Recommendations\n\n" + 
                       "\n".join(f"- {rec}" for rec in review['recommendations'])
            }
        }
        
        comments_url = f"{self.bb_api_base}/pullrequests/{self.pr_id}/comments"
        response = requests.post(comments_url, headers=headers, json=summary_comment)
        response.raise_for_status()
        
        # Post individual issue comments
        for issue in review['issues']:
            comment = {
                "content": {
                    "raw": f"**{issue['severity'].upper()} Severity Issue**\n\n"
                           f"{issue['description']}\n\n"
                           f"**Suggestion:** {issue['suggestion']}"
                },
                "inline": {
                    "path": issue['file'],
                    "to": issue['line']
                }
            }
            response = requests.post(comments_url, headers=headers, json=comment)
            response.raise_for_status()

    def run_review(self) -> bool:
        """Execute the complete review process."""
        try:
            changes = self.get_pr_changes()
            review = self.analyze_with_claude(changes)
            self.post_comments(review)
            
            # Fail the pipeline if there are any high severity issues
            has_high_severity = any(issue['severity'] == 'high' 
                                  for issue in review['issues'])
            return not has_high_severity
            
        except Exception as e:
            print(f"Error during review process: {e}")
            return False

if __name__ == "__main__":
    reviewer = ClaudePRReviewer()
    success = reviewer.run_review()
    exit(0 if success else 1)

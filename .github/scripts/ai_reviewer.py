#!/usr/bin/env python3
"""
AI Code Review Agent for Spring Boot Microservices
Uses Anthropic Claude to perform intelligent, context-aware code review
"""

import os
import sys
import json
import re
import requests

# ── Configuration from environment variables (injected by GitHub Actions) ──
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GITHUB_TOKEN      = os.environ["GITHUB_TOKEN"]
REPO_NAME         = os.environ["GITHUB_REPOSITORY"]        # e.g. "username/repo"
PR_NUMBER         = os.environ["PR_NUMBER"]
COMMIT_SHA        = os.environ["COMMIT_SHA"]

GITHUB_API        = "https://api.github.com"
ANTHROPIC_API     = "https://api.anthropic.com/v1/messages"

HEADERS_GITHUB = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept":        "application/vnd.github.v3+json",
    "X-GitHub-Api-Version": "2022-11-28"
}

HEADERS_ANTHROPIC = {
    "x-api-key":         ANTHROPIC_API_KEY,
    "anthropic-version": "2023-06-01",
    "content-type":      "application/json"
}


# ─────────────────────────────────────────────
# TOOL 1: Fetch the PR diff from GitHub API
# ─────────────────────────────────────────────
def get_pr_diff():
    """Fetches the full unified diff of the pull request."""
    url = f"{GITHUB_API}/repos/{REPO_NAME}/pulls/{PR_NUMBER}"
    headers = {**HEADERS_GITHUB, "Accept": "application/vnd.github.v3.diff"}
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    return response.text


# ─────────────────────────────────────────────
# TOOL 2: Fetch PR metadata (title, description, author)
# ─────────────────────────────────────────────
def get_pr_metadata():
    """Fetches PR title, body, author, and changed file list."""
    url = f"{GITHUB_API}/repos/{REPO_NAME}/pulls/{PR_NUMBER}"
    response = requests.get(url, headers=HEADERS_GITHUB)
    response.raise_for_status()
    data = response.json()
    return {
        "title":  data.get("title", ""),
        "body":   data.get("body", ""),
        "author": data.get("user", {}).get("login", "unknown"),
        "base":   data.get("base", {}).get("ref", "main")
    }


# ─────────────────────────────────────────────
# TOOL 3: Post a general PR comment
# ─────────────────────────────────────────────
def post_pr_comment(body: str):
    """Posts a top-level comment on the PR."""
    url = f"{GITHUB_API}/repos/{REPO_NAME}/issues/{PR_NUMBER}/comments"
    payload = {"body": body}
    response = requests.post(url, headers=HEADERS_GITHUB, json=payload)
    response.raise_for_status()
    print(f"✅ Posted PR comment successfully")


# ─────────────────────────────────────────────
# TOOL 4: Add a label to the PR
# ─────────────────────────────────────────────
def add_label(label: str):
    """Adds a label to the PR (e.g. 'needs-revision', 'ai-approved')."""
    url = f"{GITHUB_API}/repos/{REPO_NAME}/issues/{PR_NUMBER}/labels"
    payload = {"labels": [label]}
    response = requests.post(url, headers=HEADERS_GITHUB, json=payload)
    if response.status_code == 422:
        print(f"⚠️  Label '{label}' doesn't exist in repo yet. Create it first.")
    else:
        response.raise_for_status()
        print(f"✅ Added label: {label}")


# ─────────────────────────────────────────────
# AGENTIC LOOP: Call Claude with tool-use capability
# ─────────────────────────────────────────────
def call_claude_agent(diff: str, metadata: dict) -> dict:
    """
    Sends the diff to Claude with a detailed system prompt.
    Claude reasons about the code and returns structured review findings.
    This is the core agentic reasoning step.
    """

    system_prompt = """You are a senior Java architect and security expert specializing in 
Spring Boot microservices. You perform thorough, actionable code reviews.

For every pull request diff you receive, you must analyze and return a JSON response with this exact structure:

{
  "summary": "One paragraph overall assessment of the PR",
  "severity": "CRITICAL | MAJOR | MINOR | APPROVED",
  "issues": [
    {
      "file": "path/to/File.java",
      "line_hint": "approximate line or method name",
      "category": "SECURITY | BUG | PERFORMANCE | DESIGN | STYLE | SPRING_ANTIPATTERN",
      "severity": "CRITICAL | MAJOR | MINOR",
      "description": "Clear explanation of the problem",
      "suggestion": "Exact code or approach to fix it"
    }
  ],
  "positive_observations": ["Things done well in this PR"],
  "recommended_action": "APPROVE | REQUEST_CHANGES | COMMENT"
}

Focus specifically on these Spring Boot microservice concerns:
- Security: SQL injection, missing @PreAuthorize, exposed sensitive data in logs or responses
- Transaction management: missing @Transactional, wrong propagation levels
- Exception handling: swallowed exceptions, missing global @ControllerAdvice patterns  
- Performance: N+1 queries, missing pagination, synchronous calls that should be async
- Configuration: hardcoded values that should be in application.properties
- Dependency Injection: field injection instead of constructor injection
- API design: non-RESTful patterns, missing input validation (@Valid)
- Resilience: missing circuit breakers, no timeout configurations

Return ONLY the JSON. No markdown, no explanation outside the JSON."""

    user_message = f"""Please review this Pull Request:

**PR Title:** {metadata['title']}
**Author:** {metadata['author']}  
**Target Branch:** {metadata['base']}
**PR Description:** {metadata['body'] or 'No description provided'}

**Code Diff:**
```diff
{diff[:12000]}
```

Perform a thorough agentic review and return your structured JSON findings."""

    payload = {
        "model": "claude-opus-4-6",
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": user_message}
        ]
    }

    print("🤖 Sending diff to Claude for agentic analysis...")
    response = requests.post(ANTHROPIC_API, headers=HEADERS_ANTHROPIC, json=payload)
    response.raise_for_status()

    raw_text = response.json()["content"][0]["text"]
    print("✅ Claude analysis complete")

    # Parse the JSON response from Claude
    try:
        # Strip any accidental markdown fences
        clean = re.sub(r"```json|```", "", raw_text).strip()
        return json.loads(clean)
    except json.JSONDecodeError as e:
        print(f"⚠️  Could not parse Claude response as JSON: {e}")
        print(f"Raw response: {raw_text[:500]}")
        return {"summary": raw_text, "severity": "COMMENT", "issues": [], 
                "positive_observations": [], "recommended_action": "COMMENT"}


# ─────────────────────────────────────────────
# FORMAT: Turn Claude's findings into a rich GitHub comment
# ─────────────────────────────────────────────
def format_review_comment(review: dict, metadata: dict) -> str:
    """Formats Claude's structured JSON findings into a readable GitHub PR comment."""

    severity_emoji = {
        "CRITICAL": "🔴",
        "MAJOR":    "🟠",
        "MINOR":    "🟡",
        "APPROVED": "✅"
    }

    category_emoji = {
        "SECURITY":          "🔒",
        "BUG":               "🐛",
        "PERFORMANCE":       "⚡",
        "DESIGN":            "🏗️",
        "STYLE":             "✏️",
        "SPRING_ANTIPATTERN":"🍃"
    }

    overall_emoji = severity_emoji.get(review.get("severity", "MINOR"), "💬")
    lines = []

    lines.append(f"## {overall_emoji} AI Code Review — Spring Boot Microservice Analysis")
    lines.append(f"> Reviewed by Claude | PR: {metadata['title']} | Author: @{metadata['author']}")
    lines.append("")
    lines.append("### 📋 Summary")
    lines.append(review.get("summary", "No summary provided."))
    lines.append("")

    issues = review.get("issues", [])
    if issues:
        lines.append(f"### 🔍 Issues Found ({len(issues)} total)")
        lines.append("")

        # Group issues by severity
        for sev in ["CRITICAL", "MAJOR", "MINOR"]:
            sev_issues = [i for i in issues if i.get("severity") == sev]
            if sev_issues:
                lines.append(f"#### {severity_emoji.get(sev, '')} {sev} Issues")
                for idx, issue in enumerate(sev_issues, 1):
                    cat  = issue.get("category", "GENERAL")
                    emoji = category_emoji.get(cat, "💬")
                    lines.append(f"**{idx}. {emoji} {cat}** — `{issue.get('file', 'unknown')}`")
                    lines.append(f"- **Where:** {issue.get('line_hint', 'See file')}")
                    lines.append(f"- **Problem:** {issue.get('description', '')}")
                    lines.append(f"- **Fix:** {issue.get('suggestion', '')}")
                    lines.append("")

    positives = review.get("positive_observations", [])
    if positives:
        lines.append("### 👍 Well Done")
        for p in positives:
            lines.append(f"- {p}")
        lines.append("")

    action = review.get("recommended_action", "COMMENT")
    action_map = {
        "APPROVE":         "✅ **Recommendation: APPROVE** — Looks good to merge!",
        "REQUEST_CHANGES": "❌ **Recommendation: REQUEST CHANGES** — Please address the issues above before merging.",
        "COMMENT":         "💬 **Recommendation: COMMENT** — Some observations to consider."
    }
    lines.append("### 🏁 Final Verdict")
    lines.append(action_map.get(action, "💬 Review complete."))
    lines.append("")
    lines.append("---")
    lines.append("*🤖 This review was generated by an AI agent. Always apply human judgment.*")

    return "\n".join(lines)


# ─────────────────────────────────────────────
# MAIN AGENTIC ORCHESTRATION LOOP
# ─────────────────────────────────────────────
def main():
    print("🚀 Starting AI Code Review Agent...")

    # ── Step A: Gather context (agent observes the environment) ──
    print("📥 Fetching PR metadata...")
    metadata = get_pr_metadata()
    print(f"   PR: {metadata['title']} by @{metadata['author']}")

    print("📥 Fetching PR diff...")
    diff = get_pr_diff()
    print(f"   Diff size: {len(diff)} characters")

    if not diff.strip():
        print("⚠️  No diff found. Skipping review.")
        sys.exit(0)

    # ── Step B: Agent reasons using LLM (the intelligence layer) ──
    review = call_claude_agent(diff, metadata)

    # ── Step C: Agent acts based on its findings (autonomous actions) ──
    comment_body = format_review_comment(review, metadata)
    post_pr_comment(comment_body)

    # ── Step D: Agent makes decisions and applies labels ──
    severity = review.get("severity", "MINOR")
    action   = review.get("recommended_action", "COMMENT")

    if severity == "CRITICAL" or action == "REQUEST_CHANGES":
        add_label("needs-revision")
        print("🔴 Critical issues found — labeled PR as needs-revision")
    elif action == "APPROVE":
        add_label("ai-approved")
        print("✅ Code approved by AI agent")

    print(f"\n✅ AI Code Review Agent completed. Overall severity: {severity}")

    # Exit with error code if critical issues found (will fail the CI check)
    if severity == "CRITICAL":
        sys.exit(1)


if __name__ == "__main__":
    main()

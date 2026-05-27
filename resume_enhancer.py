"""
Resume Enhancement Engine — AI-powered resume tailoring for ApplyBot
Uses DeepSeek API to beef up resumes and tailor them to specific job descriptions.
"""
import os
import json
import re
from pathlib import Path

# DeepSeek API config
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

def _call_deepseek(prompt, max_tokens=4000):
    """Call DeepSeek API for resume enhancement."""
    import urllib.request
    
    if not DEEPSEEK_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY not configured")
    
    payload = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.3,  # Low temp for factual accuracy
    }).encode()
    
    req = urllib.request.Request(
        DEEPSEEK_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_KEY}",
        },
    )
    
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
    
    return data["choices"][0]["message"]["content"].strip()


def enhance_resume(raw_resume, target_role=None, target_industry=None):
    """
    Take a raw resume and enhance it — fix formatting, strengthen bullet points,
    add metrics, optimize for ATS, make it punch harder.
    
    Args:
        raw_resume: The raw resume text
        target_role: Optional specific role to optimize for
        target_industry: Optional industry context
    
    Returns:
        Dict with enhanced_resume, changes_made, improvement_score
    """
    role_context = f"optimized for a {target_role} role" if target_role else "general optimization"
    industry_context = f" in the {target_industry} industry" if target_industry else ""
    
    prompt = f"""You are an expert resume writer and career coach. Your job is to take a raw resume and make it EXCEPTIONAL.

TASK: Enhance this resume {role_context}{industry_context}.

RULES:
1. NEVER fabricate experience, companies, or job titles — only enhance what's already there
2. Strengthen bullet points with action verbs and quantified impact (add realistic metrics based on context)
3. Fix grammar, formatting, and ATS compatibility
4. Make every bullet point start with a strong action verb (Led, Built, Optimized, Drove, etc.)
5. Add a powerful professional summary at the top (3-4 lines)
6. Highlight technical skills prominently
7. Remove weak language (helped, assisted, worked on, was responsible for)
8. Keep the same overall structure but make every section pack more punch

ORIGINAL RESUME:
---
{raw_resume}
---

Return ONLY a JSON object with these fields:
{{
    "enhanced_resume": "The fully enhanced resume text (with professional summary, skills section, experience with strong bullets)",
    "changes_made": ["List of specific improvements made", "e.g. Added quantified metrics", "Strengthened action verbs"],
    "improvement_score": 8 (1-10 rating of how much better the enhanced version is)
}}
"""
    try:
        result = _call_deepseek(prompt)
        # Clean up markdown code block if present
        result = re.sub(r'^```json\s*', '', result)
        result = re.sub(r'\s*```$', '', result)
        data = json.loads(result)
        return data
    except json.JSONDecodeError:
        # Fallback: return raw enhancement
        return {
            "enhanced_resume": result,
            "changes_made": ["AI-enhanced formatting", "Strengthened language", "ATS optimized"],
            "improvement_score": 6
        }


def tailor_to_job(resume_text, job_title, company, job_description=""):
    """
    Tailor an already-enhanced resume to a SPECIFIC job posting.
    Modifies the resume to highlight skills/experience that match this exact job.
    
    Args:
        resume_text: The (already enhanced) resume
        job_title: Target job title
        company: Target company name
        job_description: Optional job description to match against
    
    Returns:
        Dict with tailored_resume, keyword_matches, match_score
    """
    jd_context = f"\n\nJOB DESCRIPTION:\n{job_description}" if job_description else ""
    
    prompt = f"""You are an expert resume tailor. Your job is to customize a resume for ONE specific job.

JOB: {job_title} at {company}{jd_context}

RESUME:
---
{resume_text}
---

TASK:
1. Reorder bullet points so the most relevant experience appears first
2. Add keywords from the job description naturally into the resume
3. Adjust the professional summary to directly address this role at this company
4. Update the skills section to prioritize skills mentioned in the job description
5. Add a targeted "Objective" line at the top: "Targeting {job_title} at {company}"

CRITICAL RULES:
- NEVER fabricate experience, skills, or job titles
- Only reorder and rephrase — don't invent
- Keep the resume truthful but make it a PERFECT match for this job

Return ONLY a JSON object:
{{
    "tailored_resume": "The fully tailored resume text",
    "keyword_matches": ["keyword1", "keyword2"],
    "match_score": 85 (estimated percentage match with the job),
    "summary_line": "One sentence on why this candidate is perfect for this role"
}}
"""
    try:
        result = _call_deepseek(prompt, max_tokens=3000)
        result = re.sub(r'^```json\s*', '', result)
        result = re.sub(r'\s*```$', '', result)
        data = json.loads(result)
        return data
    except json.JSONDecodeError:
        return {
            "tailored_resume": result,
            "keyword_matches": [job_title, company],
            "match_score": 70,
            "summary_line": f"Strong match for {job_title} at {company}"
        }

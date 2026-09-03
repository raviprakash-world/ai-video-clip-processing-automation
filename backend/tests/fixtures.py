import copy

VALID_ANALYSIS = {
    "schema_version": "1.0",
    "analysis": {
        "source_type": "video",
        "language": "en",
        "total_clips_found": 2,
        "analysis_status": "complete",
    },
    "clips": [
        {
            "clip_id": "clip_001",
            "rank": 1,
            "start_time": "00:05:20",
            "end_time": "00:06:15",
            "duration_seconds": 55,
            "viral_score": 92,
            "category": "prediction",
            "speaker": "Guest",
            "hook": "AI isn't going to replace your job, but this specific person will.",
            "title_options": {
                "curiosity": "Who Is Actually Taking Your Job?",
                "direct": "Why AI Won't Replace You (Yet)",
            },
            "caption": "Everyone is panicking about AI taking over, but the real threat is much closer to home.",
            "hashtags": ["#ai", "#artificialintelligence", "#futureofwork", "#careeradvice", "#techtrends", "#innovation"],
            "reason": "High curiosity hook addressing a universal fear of job loss.",
            "payoff": "The viewer learns that adapting to AI tools is important.",
            "context_warning": None,
            "copyright_warning": None,
        },
        {
            "clip_id": "clip_002",
            "rank": 2,
            "start_time": "00:18:10",
            "end_time": "00:18:55",
            "duration_seconds": 45,
            "viral_score": 88,
            "category": "insight",
            "speaker": "Guest",
            "hook": "We are completely misunderstanding what AGI actually means.",
            "title_options": {
                "curiosity": "The Truth About AGI Nobody Is Telling You",
                "direct": "Defining Artificial General Intelligence",
            },
            "caption": "Stop worrying about Terminator-style AI. The reality of AGI is much more subtle.",
            "hashtags": ["#agi", "#tech", "#machinelearning", "#futuretech"],
            "reason": "Challenges a common misconception immediately.",
            "payoff": "Delivers a clearer understanding of AGI.",
            "context_warning": None,
            "copyright_warning": None,
        },
    ],
}


def valid_analysis():
    return copy.deepcopy(VALID_ANALYSIS)

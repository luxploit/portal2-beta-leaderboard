from __future__ import annotations

import re
from urllib.parse import urlparse

TIME_RE = re.compile(r"^\s*(?:(\d{1,2}):)?(?:(\d{1,2}):)?(\d{1,2})(?:[\.,](\d{1,3}))?\s*$")

def parse_time_to_ms(raw: str) -> int:
    """Parse SS(.mmm), MM:SS(.mmm), or HH:MM:SS(.mmm) into milliseconds."""
    value = raw.strip()
    if not value:
        raise ValueError("Time is required.")

    parts = value.replace(",", ".").split(":")
    if len(parts) > 3:
        raise ValueError("Use SS.mmm, MM:SS.mmm, or HH:MM:SS.mmm.")

    try:
        if len(parts) == 1:
            hours = 0
            minutes = 0
            seconds_part = parts[0]
        elif len(parts) == 2:
            hours = 0
            minutes = int(parts[0])
            seconds_part = parts[1]
        else:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds_part = parts[2]

        if "." in seconds_part:
            seconds_text, millis_text = seconds_part.split(".", 1)
        else:
            seconds_text, millis_text = seconds_part, ""

        seconds = int(seconds_text)
        if not seconds_text or (millis_text and (not millis_text.isdigit() or len(millis_text) > 3)):
            raise ValueError
        
        millis = int(millis_text.ljust(3, "0")) if millis_text else 0
    except ValueError as exc:
        raise ValueError("Use SS.mmm, MM:SS.mmm, or HH:MM:SS.mmm.") from exc

    if hours < 0 or minutes < 0 or seconds < 0:
        raise ValueError("Time cannot be negative.")
    if len(parts) >= 2 and seconds >= 60:
        raise ValueError("Seconds must be below 60 when using colons.")
    if len(parts) == 3 and minutes >= 60:
        raise ValueError("Minutes must be below 60 when using hours.")

    total_ms = ((hours * 3600 + minutes * 60 + seconds) * 1000) + millis
    if total_ms <= 0:
        raise ValueError("Time must be greater than zero.")
    if total_ms > 24 * 60 * 60 * 1000:
        raise ValueError("Time must be 24 hours or less.")
    
    return total_ms

def format_time(time_ms: int) -> str:
    total_seconds, millis = divmod(time_ms, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{millis:03d}"
    
    return f"{minutes}:{seconds:02d}.{millis:03d}"

def validate_video_url(raw: str) -> str:
    value = raw.strip()
    if len(value) > 500:
        raise ValueError("Video URL is too long.")
    
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a valid http:// or https:// video URL.")
    
    return value

def ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"

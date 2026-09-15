import re
from datetime import date, timedelta
import calendar

def _word_bounds(name: str):
    return r'\b', r'\b'

def _month_name_re(name: str) -> str:
    left, right = _word_bounds(name)
    return f"{left}{re.escape(name)}{right}"

def test_extract(cleaned):
    today = date(2026, 9, 15) # Tuesday
    
    month_name_map = {
        "january": 1, "jan": 1, "ஜனவரி": 1,
        "february": 2, "feb": 2, "பிப்ரவரி": 2,
        "march": 3, "mar": 3, "மார்ச்": 3,
        "april": 4, "apr": 4, "ஏப்ரல்": 4,
        "may": 5, "மே": 5,
        "june": 6, "jun": 6, "ஜூன்": 6,
        "july": 7, "jul": 7, "ஜூலை": 7,
        "august": 8, "aug": 8, "ஆகஸ்ட்": 8, "ஆக": 8,
        "september": 9, "sep": 9, "செப்டம்பர்": 9,
        "october": 10, "oct": 10, "அக்டோபர்": 10,
        "november": 11, "nov": 11, "நவம்பர்": 11,
        "december": 12, "dec": 12, "டிசம்பர்": 12
    }
    
    weekday_map = {
        "monday": 0, "mon": 0, "திங்கள்": 0, "திங்கட்கிழமை": 0,
        "tuesday": 1, "tue": 1, "செவ்வாய்": 1, "செவ்வாய்க்கிழமை": 1,
        "wednesday": 2, "wed": 2, "புதன்": 2, "புதன்கிழமை": 2,
        "thursday": 3, "thu": 3, "வியாழன்": 3, "வியாழக்கிழமை": 3,
        "friday": 4, "fri": 4, "வெள்ளி": 4, "வெள்ளிக்கிழமை": 4,
        "saturday": 5, "sat": 5, "சனி": 5, "சனிக்கிழமை": 5,
        "sunday": 6, "sun": 6, "ஞாயிறு": 6, "ஞாயிற்றுக்கிழமை": 6
    }
    found_weekdays = []
    for day_name, day_idx in weekday_map.items():
        _l, _r = _word_bounds(day_name)
        for m in re.finditer(_l + r'(?:on\s+|this\s+|next\s+|last\s+|past\s+)?' + re.escape(day_name) + _r, cleaned):
            context = cleaned[max(0, m.start()-15):m.end()]
            is_last = bool(re.search(r'\b(?:last|past|முந்தைய|கடந்த|சென்ற)\b', context))
            is_next = bool(re.search(r'\b(?:next|coming|அடுத்த)\b', context))
            
            target_date = None
            month_match = None
            year_match = None
            for m_name, m_num in month_name_map.items():
                match = re.search(_month_name_re(m_name) + r'(?:\s+(\d{4}))?', cleaned)
                if match:
                    month_match = m_num
                    year_match = int(match.group(1)) if match.group(1) else today.year
                    break
            
            if month_match:
                d = date(year_match, month_match, 1)
                days_ahead = (day_idx - d.weekday()) % 7
                target_date = d + timedelta(days=days_ahead)
            else:
                if is_last:
                    days_behind = (today.weekday() - day_idx) % 7
                    if days_behind == 0: days_behind = 7
                    target_date = today - timedelta(days=days_behind)
                elif is_next:
                    days_ahead = (day_idx - today.weekday()) % 7
                    if days_ahead == 0: days_ahead = 7
                    target_date = today + timedelta(days=days_ahead)
                else:
                    days_behind = (today.weekday() - day_idx) % 7
                    target_date = today - timedelta(days=days_behind)
            
            found_weekdays.append(target_date)

    if len(found_weekdays) >= 2:
        found_weekdays.sort()
        return found_weekdays[0], found_weekdays[-1]
    elif len(found_weekdays) == 1:
        return found_weekdays[0], found_weekdays[0]
    return None

print("between monday and tuesday:", test_extract("between monday and tuesday"))
print("last monday:", test_extract("last monday"))
print("monday in june 2026:", test_extract("monday in june 2026"))

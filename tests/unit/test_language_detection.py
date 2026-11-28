"""
Unit tests for language detection (Tamil, Tanglish, English, and negation).
"""
import pytest
from backend.services.rag_language import detect_language


def test_detect_english():
    assert detect_language("Show my pending applications") == "en"
    assert detect_language("What is the status of application 2026/0154/28/000001?") == "en"
    assert detect_language("How many field visits are scheduled for tomorrow?") == "en"
    assert detect_language("Applications in Tamil Nadu") == "en"  # "Tamil Nadu" must not trigger Tamil


def test_detect_pure_tamil():
    # Sentences with > 50% Tamil script characters
    assert detect_language("நிலுவையில் உள்ள விண்ணப்பங்களைக் காட்டு") == "ta"
    assert detect_language("எனது அதிகார வரம்பில் எத்தனை விண்ணப்பங்கள் உள்ளன?") == "ta"
    assert detect_language("கள ஆய்வு விவரங்கள் என்ன?") == "ta"
    assert detect_language("விண்ணப்பதாரர் பெயர் என்ன?") == "ta"


def test_detect_tanglish():
    # Romanized Tamil words or mixed scripts
    assert detect_language("pending list kaattu") == "tanglish"
    assert detect_language("enna status irukku") == "tanglish"
    assert detect_language("eppadi field visit schedule panradhu") == "tanglish"
    assert detect_language("Survey 145 enga irukku?") == "tanglish"
    assert detect_language("CSC applications evlo pending") == "tanglish"


def test_detect_explicit_tamil_request():
    assert detect_language("Explain the workflow in Tamil") == "ta"
    assert detect_language("Reply in Tamil please") == "ta"
    assert detect_language("tamil la sollu") == "ta"
    assert detect_language("தமிழில் சொல்லவும்") == "ta"


def test_detect_negated_tamil_request():
    # Explicitly asking NOT to use Tamil must stay English
    assert detect_language("Don't explain in Tamil, reply in English") == "en"
    assert detect_language("Do not answer in Tamil") == "en"
    assert detect_language("No Tamil, use English only") == "en"
    assert detect_language("Never respond in Tamil") == "en"


def test_detect_empty_or_digits_only():
    assert detect_language("") == "en"
    assert detect_language("2026/0154/28/000001") == "en"
    assert detect_language("12345 67890") == "en"


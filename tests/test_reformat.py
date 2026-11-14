"""'roman urdu mai batao' pichle jawab par hukm hai (naya sawal nahi) + Roman Urdu pehchan."""
from app.services.router import is_reformat_request, roman_urdu_signal


def test_reformat_requests_are_detected():
    for q in ["roman urdu mai batao", "english mein batao", "short karo", "dobara samjhao",
              "simple words mein bata do", "urdu mai", "summary do", "translate to english"]:
        assert is_reformat_request(q), q


def test_real_questions_are_not_reformat_requests():
    for q in ["21 CFR 820 ke mutabiq foreign manufacturers ke liye kya rules hain?", "hamara MOQ kya hai",
              "scalpel kya hota hai", "what is urdu 2 meter", "FDA ke rules english mein batao",
              "abhi fda k law mai koi strict point hai?",
              "draft buyer email in english for forceps order to germany please"]:
        assert not is_reformat_request(q), q


def test_roman_urdu_signal():
    assert roman_urdu_signal("abhi fda k law mai koi strict point hai?")
    assert roman_urdu_signal("USA bhejne wale instruments ki packaging par kya likhna zaroori hai?")
    assert not roman_urdu_signal("What are FDA rules for forceps")

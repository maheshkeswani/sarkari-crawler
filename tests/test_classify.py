import pytest

from sarkari_crawler.classify import (
    CORRIGENDUM,
    NOISE,
    NOTIFICATION,
    UNCLASSIFIED,
    classify,
    classify_link,
    classify_text,
    is_candidate_link,
)
from sarkari_crawler.urls import is_http_url, normalize_url


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Detailed Advertisement for recruitment of Junior Engineers 2026", NOTIFICATION),
        ("Notification: Combined Graduate Level Examination 2026", NOTIFICATION),
        ("Applications invited for 120 posts of Assistant", NOTIFICATION),
        ("कनिष्ठ सहायक भर्ती विज्ञापन 2026", NOTIFICATION),
        ("Corrigendum to Advt. No. 05/2026", CORRIGENDUM),
        ("Extension of last date for online applications", CORRIGENDUM),
        ("Advertisement No. 3/2026 cancelled", CORRIGENDUM),
        ("शुद्धि पत्र - विज्ञापन संख्या 05/2026", CORRIGENDUM),
        ("Final Result of CGL 2025", NOISE),
        ("Download Admit Card for Constable recruitment", NOISE),
        ("Answer key for recruitment exam", NOISE),
        ("Tender for supply of computers", NOISE),
        ("Syllabus for Junior Assistant", NOISE),
        ("परीक्षा परिणाम घोषित", NOISE),
        ("Click here", UNCLASSIFIED),
    ],
)
def test_classify(text, expected):
    assert classify(text) == expected


def test_link_url_only_fills_in_when_the_text_says_nothing():
    assert classify_link("Download", "https://x.gov.in/uploads/Recruitment_Advt_05_2026.pdf") == NOTIFICATION
    assert classify_link("Download", "https://x.gov.in/uploads/final_result_2026.pdf") == NOISE
    assert classify_link("Result of written exam", "https://x.gov.in/recruitment.pdf") == NOISE


def test_candidate_links():
    assert is_candidate_link("Download", "https://x.gov.in/a.PDF", UNCLASSIFIED)  # every PDF
    assert is_candidate_link("Recruitment of Assistant Professors 2026", "https://x.gov.in/notice/12", NOTIFICATION)
    assert not is_candidate_link("Recruitment", "https://x.gov.in/recruitment", NOTIFICATION)  # a menu item
    assert not is_candidate_link("About us and our history", "https://x.gov.in/about", UNCLASSIFIED)


def test_classify_text_looks_at_the_title_area_for_noise():
    notice = "Staff Selection Commission\nNOTICE\nRecruitment of Constables 2026\n" + "Admit cards will be issued later. " * 5 + "Vacancies: 500"
    assert classify_text(notice) == NOTIFICATION
    assert classify_text("Result of Combined Graduate Level Examination 2025\nRoll numbers of selected candidates") == NOISE
    assert classify_text("CORRIGENDUM\nThe last date is extended") == CORRIGENDUM
    assert classify_text("Minutes of the meeting held on 3 March") == UNCLASSIFIED


@pytest.mark.parametrize(
    "url, normalized",
    [
        ("https://www.SSC.gov.in/notices/a.pdf", "ssc.gov.in/notices/a.pdf"),
        ("http://ssc.gov.in/notices/a.pdf#page=2", "ssc.gov.in/notices/a.pdf"),
        ("https://ssc.gov.in//notices///a.pdf", "ssc.gov.in/notices/a.pdf"),
        ("https://ssc.gov.in/notices/", "ssc.gov.in/notices"),
        ("https://ssc.gov.in", "ssc.gov.in/"),
        ("https://ssc.gov.in/view?b=2&a=1&utm_source=x&fbclid=y", "ssc.gov.in/view?a=1&b=2"),
        ("https://ssc.gov.in:8443/a", "ssc.gov.in:8443/a"),
        ("https://ssc.gov.in:443/a", "ssc.gov.in/a"),
        ("https://ssc.gov.in/Notice.PDF", "ssc.gov.in/Notice.PDF"),
    ],
)
def test_normalize_url(url, normalized):
    assert normalize_url(url) == normalized


def test_is_http_url():
    assert is_http_url("https://x.gov.in/a")
    assert not is_http_url("javascript:void(0)") and not is_http_url("mailto:a@b.c") and not is_http_url("/relative")


@pytest.mark.parametrize(
    "text",
    [
        "APPLY ONLINE (02.11.2026 TO 23.11.2026)",
        "Click here to apply for Junior Associates recruitment",
        "Candidate login for recruitment of officers",
    ],
)
def test_portal_links_are_not_notices(text):
    assert not is_candidate_link(text, "https://bank.example.in/careers/apply", classify(text))


@pytest.mark.parametrize(
    "text",
    [
        "Selection Panel for the post of Scientist against Advertisement No. 5",
        "ADVT NO. 01/2026 - Date of conducting the Skill Test: 01.12.2026",
        "Bed Occupancy/Vacancy Dashboard Profile",
        "List of eligible candidates for recruitment of Clerks",
    ],
)
def test_more_noise(text):
    assert classify(text) == NOISE


@pytest.mark.parametrize(
    "text",
    [
        "Notification regarding Interview/Viva-Voce for the post of Research Assistant",
        "Download e-Admission Certificate for Screening Test",
        "Press Release regarding Combined Civil Services Main Examination Advt.No.01/2026",
        "Appointment Letter Regarding Direct Recruitment of Fourth Class Employees",
        "Important Notice Regarding Document Upload (Veterinary Officer) Advertisement 2/2026-27",
        "New / Amendment of Service Rules",
    ],
)
def test_state_board_noise(text):
    assert classify(text) == NOISE


def test_html_link_path_does_not_classify_it():
    assert classify_link("Last page Last »", "https://keralapsc.gov.in/notifications?page=9") == UNCLASSIFIED
    assert not is_candidate_link("Last page Last »", "https://keralapsc.gov.in/notifications?page=9", UNCLASSIFIED)


def test_lenient_tls_context():
    import ssl

    from sarkari_crawler.fetch import lenient_tls_context

    context = lenient_tls_context()
    assert context.verify_mode == ssl.CERT_NONE and not context.check_hostname
    assert context.options & getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)


def test_walk_in_interview_is_a_notification():
    assert classify("Walk-in interview for recruitment of Junior Residents on contract") == NOTIFICATION
    assert classify("Interview schedule for the post of Junior Resident") == NOISE


@pytest.mark.parametrize(
    "text",
    [
        "MOCK TEST LINK FOR CBT FOR THE POST OF HC(RO) IN BSF COMN",
        "23/10/2026 Question Paper for Senior Scientific Officer – 2025",
        "Punjab Civil Service Executive Branch Class 1 Rules 1976 With Amendment 2011",
        "Assam Public Services Combined Competitive Examination (Amendment) Rules, 2019",
        "Notification regarding rescheduling of Forest Ranger Examination (Advt. No. 22/2025)",
        "Important Notice :- Regarding Postponement of 72nd CCE (Preliminary) Competitive Exam",
        "Annexures-II",
        "IN Calendar - 2026",
        "Deficiency of documents for the post of Assistant Architect",
        "List of 329 empanaled advocates for RO Maharashtra",
        "महंगाई भत्ते की दरों में संशोधन के संबंध में",
    ],
)
def test_queue_noise_seen_in_real_crawls(text):
    assert classify(text) == NOISE


@pytest.mark.parametrize(
    "text",
    [
        "VACANCY NOTICE/ ADVERTISEMENT NO. 03/2026",
        "Advt No. URSC:04:2026 dated 24.10.2026 - Inviting online applications for the posts of Technician",
        "PUBLIC NOTICE REGARDING RECRUITMENT TO 75 POSTS OF JUNIOR AUDITOR GROUP-B",
        "एम्स नई दिल्ली में आईसीएमआर वित्त पोषित अस्थायी पद हेतु आवेदन आमंत्रित किए जाते है",
        "Recruitment of Assistant Professor in Medical Colleges (Super Specialist) Department Advt. No. 12/2026",
    ],
)
def test_real_notices_still_pass(text):
    assert classify(text) == NOTIFICATION

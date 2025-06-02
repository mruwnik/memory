import hashlib
import itertools
from datetime import datetime
from unittest.mock import patch

import pytest
import voyageai

import memory.common.qdrant as qdrant_tools
from memory.common import extract
from memory.common.db.models.source_item import SourceItem
from memory.common.db.models.source_items import (
    AgentObservation,
)
from memory.common.embedding import embed_source_item, embed_text
from memory.workers.tasks.content_processing import push_to_qdrant
from tests.data.contents import SAMPLE_MARKDOWN


@pytest.fixture
def real_voyage_client(mock_voyage_client):
    real_client = mock_voyage_client.real_client
    with patch.object(voyageai, "Client", real_client):
        yield real_client


def test_real_source_item_embeddings(real_voyage_client, qdrant):
    item = SourceItem(
        id=1,
        content=SAMPLE_MARKDOWN,
        mime_type="text/html",
        modality="text",
        sha256=hashlib.sha256(SAMPLE_MARKDOWN.encode("utf-8")).hexdigest(),
        size=len(SAMPLE_MARKDOWN),
        tags=["bla"],
        embed_status="QUEUED",
    )

    part1, part2, summary = embed_source_item(item)
    part1.id = "00000000-0000-0000-0000-000000000000"  # type: ignore
    part2.id = "00000000-0000-0000-0000-000000000001"  # type: ignore
    summary.id = "00000000-0000-0000-0000-000000000002"  # type: ignore
    push_to_qdrant([item])

    queries = {
        "how have programming languages changed?": [0.6756747, 0.6319432, 0.26348075],
        "evolution of programming languages since 1940s": [0.690, 0.594, 0.330],
        "functional programming paradigms and immutability": [0.507, 0.412, 0.276],
        "memory safety in systems programming languages": [0.487, 0.458, 0.348],
        "FORTRAN and COBOL pioneering human-readable code": [0.535, 0.458, 0.296],
        "Rust and type systems for reliable code": [0.585, 0.506, 0.456],
        "WebAssembly high-performance web computing": [0.469, 0.426, 0.296],
        "object-oriented programming innovations": [0.510, 0.492, 0.333],
        "cloud computing and distributed systems": [0.40005407, 0.56048, 0.37348732],
        "AI-assisted code generation trends": [0.51078045, 0.5828345, 0.31309962],
        "microservices and polyglot programming": [0.5072756, 0.63991153, 0.38507754],
        "Python JavaScript democratizing programming": [0.524, 0.517, 0.320],
        "software development methodologies": [0.454, 0.440, 0.356],
        "computer science history": [0.517, 0.454, 0.299],
        "programming paradigms comparison": [0.589, 0.525, 0.352],
        "developer tools and ecosystems": [0.42297083, 0.52246743, 0.39521465],
        "modern computing trends": [0.5172996, 0.5883902, 0.30886292],
        "database query languages": [0.47420773, 0.48987937, 0.41980737],
        "network programming protocols": [0.3547029, 0.42228842, 0.39325726],
        "machine learning algorithms": [0.39660394, 0.47512275, 0.45423454],
        "web browser technologies": [0.467, 0.449, 0.439],
        "software architecture patterns": [0.4430701, 0.4969077, 0.3775082],
        "mobile app user interface design": [0.2754, 0.332, 0.3863],
        "cybersecurity threat detection": [0.3436677, 0.38349956, 0.36111486],
        "project management methodologies": [0.3377, 0.34, 0.3573],
        "cooking Italian pasta recipes": [0.2627, 0.2388, 0.3065],
        "professional basketball statistics": [0.2811, 0.2454, 0.3411],
        "gardening tips for beginners": [0.2953, 0.2848, 0.3309],
        "travel destinations in Europe": [0.2595, 0.2514, 0.3039],
        "classical music composers": [0.3066, 0.2838, 0.3173],
    }
    for query, (p1, p2, s) in queries.items():
        search_vector = embed_text(
            [extract.DataChunk(data=[query])], input_type="query"
        )[0]
        results = qdrant_tools.search_vectors(qdrant, "text", search_vector)
        expected = sorted(
            [
                (part1.id, p1),
                (part2.id, p2),
                (summary.id, s),
            ],
            key=lambda x: x[1],
            reverse=True,
        )
        assert [(r.id, pytest.approx(r.score, abs=0.1)) for r in results] == expected


EXPECTED_OBSERVATION_RESULTS = {
    "What does the user think about functional programming?": {
        "semantic": [
            (
                0.7104,
                "The user believes functional programming leads to better code quality",
            ),
            (0.6792, "I prefer functional programming over OOP"),
            (
                0.6772,
                "Subject: programming_philosophy | Type: belief | Observation: The user believes functional programming leads to better code quality | Quote: Functional programming produces more maintainable code",
            ),
            (
                0.6677,
                "Subject: programming_paradigms | Type: preference | Observation: The user prefers functional programming over OOP | Quote: I prefer functional programming over OOP",
            ),
        ],
        "temporal": [
            (
                0.5816,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality | Confidence: 0.8",
            ),
            (
                0.5246,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP | Confidence: 0.8",
            ),
            (
                0.5214,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky | Confidence: 0.8",
            ),
            (
                0.4645,
                "Time: 12:00 on Wednesday (afternoon) | Subject: refactoring | Observation: The user always refactors to pure functions | Confidence: 0.8",
            ),
        ],
    },
    "Does the user prefer functional or object-oriented programming?": {
        "semantic": [
            (0.7718, "The user prefers functional programming over OOP"),
            (
                0.754,
                "Subject: programming_paradigms | Type: preference | Observation: The user prefers functional programming over OOP | Quote: I prefer functional programming over OOP",
            ),
            (0.7454, "I prefer functional programming over OOP"),
            (
                0.6541,
                "Subject: programming_philosophy | Type: belief | Observation: The user believes functional programming leads to better code quality | Quote: Functional programming produces more maintainable code",
            ),
        ],
        "temporal": [
            (
                0.6188,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP | Confidence: 0.8",
            ),
            (
                0.5902,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality | Confidence: 0.8",
            ),
            (
                0.5144,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky | Confidence: 0.8",
            ),
            (
                0.4989,
                "Time: 12:00 on Wednesday (afternoon) | Subject: refactoring | Observation: The user always refactors to pure functions | Confidence: 0.8",
            ),
        ],
    },
    "What are the user's beliefs about code quality?": {
        "semantic": [
            (0.6925, "The user believes code reviews are essential for quality"),
            (
                0.68,
                "The user believes functional programming leads to better code quality",
            ),
            (
                0.6524,
                "Subject: code_quality | Type: belief | Observation: The user believes code reviews are essential for quality | Quote: Code reviews catch bugs that automated testing misses",
            ),
            (
                0.6466,
                "Subject: programming_philosophy | Type: belief | Observation: The user believes functional programming leads to better code quality | Quote: Functional programming produces more maintainable code",
            ),
        ],
        "temporal": [
            (
                0.5544,
                "Time: 12:00 on Wednesday (afternoon) | Subject: code_quality | Observation: The user believes code reviews are essential for quality | Confidence: 0.8",
            ),
            (
                0.5397,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality | Confidence: 0.8",
            ),
            (
                0.4931,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes | Confidence: 0.8",
            ),
            (
                0.4674,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky | Confidence: 0.8",
            ),
        ],
    },
    "How does the user approach debugging code?": {
        "semantic": [
            (
                0.7011,
                "Subject: debugging_approach | Type: behavior | Observation: The user debugs by adding print statements rather than using a debugger | Quote: When debugging, I just add console.log everywhere",
            ),
            (
                0.6962,
                "The user debugs by adding print statements rather than using a debugger",
            ),
            (0.6788, "When debugging, I just add console.log everywhere"),
            (
                0.5357,
                "Subject: code_quality | Type: belief | Observation: The user believes code reviews are essential for quality | Quote: Code reviews catch bugs that automated testing misses",
            ),
        ],
        "temporal": [
            (
                0.6252,
                "Time: 12:00 on Wednesday (afternoon) | Subject: debugging_approach | Observation: The user debugs by adding print statements rather than using a debugger | Confidence: 0.8",
            ),
            (
                0.476,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces | Confidence: 0.8",
            ),
            (
                0.4424,
                "Time: 12:00 on Wednesday (afternoon) | Subject: version_control_style | Observation: The user prefers small, focused commits over large feature branches | Confidence: 0.8",
            ),
            (
                0.4402,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes | Confidence: 0.8",
            ),
        ],
    },
    "What are the user's git and version control habits?": {
        "semantic": [
            (
                0.6474,
                "Subject: version_control_style | Type: preference | Observation: The user prefers small, focused commits over large feature branches | Quote: I like to commit small, logical changes frequently",
            ),
            (0.6424, "I like to commit small, logical changes frequently"),
            (
                0.5961,
                "The user prefers small, focused commits over large feature branches",
            ),
            (
                0.5806,
                "Subject: git_habits | Type: behavior | Observation: The user writes commit messages in present tense | Quote: Fix bug in parser instead of Fixed bug in parser",
            ),
        ],
        "temporal": [
            (
                0.6174,
                "Time: 12:00 on Wednesday (afternoon) | Subject: version_control_style | Observation: The user prefers small, focused commits over large feature branches | Confidence: 0.8",
            ),
            (
                0.5733,
                "Time: 12:00 on Wednesday (afternoon) | Subject: git_habits | Observation: The user writes commit messages in present tense | Confidence: 0.8",
            ),
            (
                0.4848,
                "Time: 12:00 on Wednesday (afternoon) | Subject: editor_preference | Observation: The user prefers Vim over VS Code for editing | Confidence: 0.8",
            ),
            (
                0.4604,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces | Confidence: 0.8",
            ),
        ],
    },
    "When does the user prefer to work?": {
        "semantic": [
            (0.6806, "The user prefers working late at night"),
            (
                0.6792,
                "Subject: work_schedule | Type: behavior | Observation: The user prefers working late at night | Quote: I do my best coding between 10pm and 2am",
            ),
            (0.6439, "I do my best coding between 10pm and 2am"),
            (0.5528, "I use 25-minute work intervals with 5-minute breaks"),
        ],
        "temporal": [
            (
                0.7023,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_schedule | Observation: The user prefers working late at night | Confidence: 0.8",
            ),
            (
                0.6395,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI | Confidence: 0.8",
            ),
            (
                0.6375,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work | Confidence: 0.8",
            ),
            (
                0.6254,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems | Confidence: 0.8",
            ),
        ],
    },
    "How does the user handle productivity and time management?": {
        "semantic": [
            (
                0.579,
                "Subject: productivity_methods | Type: behavior | Observation: The user takes breaks every 25 minutes using the Pomodoro technique | Quote: I use 25-minute work intervals with 5-minute breaks",
            ),
            (0.5731, "I use 25-minute work intervals with 5-minute breaks"),
            (
                0.5284,
                "The user takes breaks every 25 minutes using the Pomodoro technique",
            ),
            (0.5153, "I do my best coding between 10pm and 2am"),
        ],
        "temporal": [
            (
                0.5705,
                "Time: 12:00 on Wednesday (afternoon) | Subject: productivity_methods | Observation: The user takes breaks every 25 minutes using the Pomodoro technique | Confidence: 0.8",
            ),
            (
                0.5023,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work | Confidence: 0.8",
            ),
            (
                0.4631,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_schedule | Observation: The user prefers working late at night | Confidence: 0.8",
            ),
            (
                0.4626,
                "Time: 12:00 on Wednesday (afternoon) | Subject: documentation_habits | Observation: The user always writes documentation before implementing features | Confidence: 0.8",
            ),
        ],
    },
    "What editor does the user prefer?": {
        "semantic": [
            (
                0.6394,
                "Subject: editor_preference | Type: preference | Observation: The user prefers Vim over VS Code for editing | Quote: Vim makes me more productive than any modern editor",
            ),
            (0.6241, "The user prefers Vim over VS Code for editing"),
            (0.5528, "Vim makes me more productive than any modern editor"),
            (0.4887, "The user claims to prefer tabs but their code uses spaces"),
        ],
        "temporal": [
            (
                0.5701,
                "Time: 12:00 on Wednesday (afternoon) | Subject: editor_preference | Observation: The user prefers Vim over VS Code for editing | Confidence: 0.8",
            ),
            (
                0.4557,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces | Confidence: 0.8",
            ),
            (
                0.4322,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI | Confidence: 0.8",
            ),
            (
                0.4283,
                "Time: 12:00 on Wednesday (afternoon) | Subject: database_preference | Observation: The user prefers PostgreSQL over MongoDB for most applications | Confidence: 0.8",
            ),
        ],
    },
    "What databases does the user like to use?": {
        "semantic": [
            (
                0.6328,
                "Subject: database_preference | Type: preference | Observation: The user prefers PostgreSQL over MongoDB for most applications | Quote: Relational databases handle complex queries better than document stores",
            ),
            (0.5992, "The user prefers PostgreSQL over MongoDB for most applications"),
            (
                0.5352,
                "Subject: domain_preference | Type: preference | Observation: The user prefers working on backend systems over frontend UI | Quote: I find backend logic more interesting than UI work",
            ),
            (0.5186, "The user prefers working on backend systems over frontend UI"),
        ],
        "temporal": [
            (
                0.5599,
                "Time: 12:00 on Wednesday (afternoon) | Subject: database_preference | Observation: The user prefers PostgreSQL over MongoDB for most applications | Confidence: 0.8",
            ),
            (
                0.4617,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI | Confidence: 0.8",
            ),
            (
                0.4445,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript | Confidence: 0.8",
            ),
            (
                0.4365,
                "Time: 12:00 on Wednesday (afternoon) | Subject: editor_preference | Observation: The user prefers Vim over VS Code for editing | Confidence: 0.8",
            ),
        ],
    },
    "What programming languages does the user work with?": {
        "semantic": [
            (0.7255, "The user primarily works with Python and JavaScript"),
            (0.6954, "Most of my work is in Python backend and React frontend"),
            (
                0.6874,
                "Subject: primary_languages | Type: general | Observation: The user primarily works with Python and JavaScript | Quote: Most of my work is in Python backend and React frontend",
            ),
            (0.6098, "I'm picking up Rust on weekends"),
        ],
        "temporal": [
            (
                0.5939,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript | Confidence: 0.8",
            ),
            (
                0.4679,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience | Confidence: 0.8",
            ),
            (
                0.4623,
                "Time: 12:00 on Wednesday (afternoon) | Subject: learning_activities | Observation: The user is currently learning Rust in their spare time | Confidence: 0.8",
            ),
            (
                0.4514,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality | Confidence: 0.8",
            ),
        ],
    },
    "What is the user's programming experience level?": {
        "semantic": [
            (0.6664, "The user has 8 years of professional programming experience"),
            (
                0.6565,
                "Subject: experience_level | Type: general | Observation: The user has 8 years of professional programming experience | Quote: I've been coding professionally for 8 years",
            ),
            (0.5949, "I've been coding professionally for 8 years"),
            (0.5641, "The user is currently learning Rust in their spare time"),
        ],
        "temporal": [
            (
                0.5991,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience | Confidence: 0.8",
            ),
            (
                0.5041,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript | Confidence: 0.8",
            ),
            (
                0.4917,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality | Confidence: 0.8",
            ),
            (
                0.4817,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP | Confidence: 0.8",
            ),
        ],
    },
    "Where did the user study computer science?": {
        "semantic": [
            (0.6863, "I studied CS at Stanford"),
            (0.649, "The user graduated with a Computer Science degree from Stanford"),
            (
                0.6344,
                "Subject: education_background | Type: general | Observation: The user graduated with a Computer Science degree from Stanford | Quote: I studied CS at Stanford",
            ),
            (0.4592, "The user is currently learning Rust in their spare time"),
        ],
        "temporal": [
            (
                0.5455,
                "Time: 12:00 on Wednesday (afternoon) | Subject: education_background | Observation: The user graduated with a Computer Science degree from Stanford | Confidence: 0.8",
            ),
            (
                0.3842,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience | Confidence: 0.8",
            ),
            (
                0.3792,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript | Confidence: 0.8",
            ),
            (
                0.3781,
                "Time: 12:00 on Wednesday (afternoon) | Subject: learning_activities | Observation: The user is currently learning Rust in their spare time | Confidence: 0.8",
            ),
        ],
    },
    "What kind of company does the user work at?": {
        "semantic": [
            (0.6308, "The user works at a mid-size startup with 50 employees"),
            (
                0.5371,
                "Subject: company_size | Type: general | Observation: The user works at a mid-size startup with 50 employees | Quote: Our company has about 50 people",
            ),
            (0.5253, "Most of my work is in Python backend and React frontend"),
            (0.4902, "I've been coding professionally for 8 years"),
        ],
        "temporal": [
            (
                0.5309,
                "Time: 12:00 on Wednesday (afternoon) | Subject: company_size | Observation: The user works at a mid-size startup with 50 employees | Confidence: 0.8",
            ),
            (
                0.4329,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work | Confidence: 0.8",
            ),
            (
                0.4323,
                "Time: 12:00 on Wednesday (afternoon) | Subject: education_background | Observation: The user graduated with a Computer Science degree from Stanford | Confidence: 0.8",
            ),
            (
                0.419,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_schedule | Observation: The user prefers working late at night | Confidence: 0.8",
            ),
        ],
    },
    "What does the user think about AI replacing programmers?": {
        "semantic": [
            (
                0.5965,
                "Subject: ai_future | Type: belief | Observation: The user thinks AI will replace most software developers within 10 years | Quote: AI will make most programmers obsolete by 2035",
            ),
            (
                0.572,
                "The user thinks AI will replace most software developers within 10 years",
            ),
            (0.5715, "AI will make most programmers obsolete by 2035"),
            (
                0.4344,
                "The user believes functional programming leads to better code quality",
            ),
        ],
        "temporal": [
            (
                0.4629,
                "Time: 12:00 on Wednesday (afternoon) | Subject: ai_future | Observation: The user thinks AI will replace most software developers within 10 years | Confidence: 0.8",
            ),
            (
                0.362,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality | Confidence: 0.8",
            ),
            (
                0.3308,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes | Confidence: 0.8",
            ),
            (
                0.328,
                "Time: 12:00 on Wednesday (afternoon) | Subject: typescript_opinion | Observation: The user now says they love TypeScript but previously called it verbose | Confidence: 0.8",
            ),
        ],
    },
    "What are the user's views on artificial intelligence?": {
        "semantic": [
            (
                0.5885,
                "Subject: ai_future | Type: belief | Observation: The user thinks AI will replace most software developers within 10 years | Quote: AI will make most programmers obsolete by 2035",
            ),
            (
                0.5661,
                "The user thinks AI will replace most software developers within 10 years",
            ),
            (0.5133, "AI will make most programmers obsolete by 2035"),
            (0.4927, "I find backend logic more interesting than UI work"),
        ],
        "temporal": [
            (
                0.5399,
                "Time: 12:00 on Wednesday (afternoon) | Subject: ai_future | Observation: The user thinks AI will replace most software developers within 10 years | Confidence: 0.8",
            ),
            (
                0.4353,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality | Confidence: 0.8",
            ),
            (
                0.4223,
                "Time: 12:00 on Wednesday (afternoon) | Subject: humans | Observation: The user thinks that all men must die. | Confidence: 0.8",
            ),
            (
                0.4219,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky | Confidence: 0.8",
            ),
        ],
    },
    "Has the user changed their mind about TypeScript?": {
        "semantic": [
            (
                0.6174,
                "The user now says they love TypeScript but previously called it verbose",
            ),
            (
                0.5757,
                "Subject: typescript_opinion | Type: contradiction | Observation: The user now says they love TypeScript but previously called it verbose | Quote: TypeScript has too much boilerplate vs TypeScript makes my code so much cleaner",
            ),
            (
                0.4924,
                "TypeScript has too much boilerplate vs TypeScript makes my code so much cleaner",
            ),
            (0.4157, "The user always refactors to pure functions"),
        ],
        "temporal": [
            (
                0.5631,
                "Time: 12:00 on Wednesday (afternoon) | Subject: typescript_opinion | Observation: The user now says they love TypeScript but previously called it verbose | Confidence: 0.8",
            ),
            (
                0.4016,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces | Confidence: 0.8",
            ),
            (
                0.3827,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript | Confidence: 0.8",
            ),
            (
                0.3825,
                "Time: 12:00 on Wednesday (afternoon) | Subject: editor_preference | Observation: The user prefers Vim over VS Code for editing | Confidence: 0.8",
            ),
        ],
    },
    "Are there any contradictions in the user's preferences?": {
        "semantic": [
            (0.536, "The user claims to prefer tabs but their code uses spaces"),
            (
                0.5353,
                "Subject: indentation_preference | Type: contradiction | Observation: The user claims to prefer tabs but their code uses spaces | Quote: Tabs are better than spaces vs code consistently uses 2-space indentation",
            ),
            (
                0.5321,
                "Subject: pure_functions | Type: contradiction | Observation: The user said pure functions are yucky | Quote: Pure functions are yucky",
            ),
            (
                0.5058,
                "Subject: typescript_opinion | Type: contradiction | Observation: The user now says they love TypeScript but previously called it verbose | Quote: TypeScript has too much boilerplate vs TypeScript makes my code so much cleaner",
            ),
        ],
        "temporal": [
            (
                0.4763,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces | Confidence: 0.8",
            ),
            (
                0.4693,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI | Confidence: 0.8",
            ),
            (
                0.4681,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky | Confidence: 0.8",
            ),
            (
                0.4586,
                "Time: 12:00 on Wednesday (afternoon) | Subject: typescript_opinion | Observation: The user now says they love TypeScript but previously called it verbose | Confidence: 0.8",
            ),
        ],
    },
    "What does the user think about software testing?": {
        "semantic": [
            (
                0.6386,
                "Subject: testing_philosophy | Type: belief | Observation: The user believes unit tests are a waste of time for prototypes | Quote: Writing tests for throwaway code slows development",
            ),
            (0.6222, "The user believes unit tests are a waste of time for prototypes"),
            (
                0.6152,
                "Subject: code_quality | Type: belief | Observation: The user believes code reviews are essential for quality | Quote: Code reviews catch bugs that automated testing misses",
            ),
            (0.6036, "The user believes code reviews are essential for quality"),
        ],
        "temporal": [
            (
                0.5881,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes | Confidence: 0.8",
            ),
            (
                0.5074,
                "Time: 12:00 on Wednesday (afternoon) | Subject: code_quality | Observation: The user believes code reviews are essential for quality | Confidence: 0.8",
            ),
            (
                0.4863,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality | Confidence: 0.8",
            ),
            (
                0.4748,
                "Time: 12:00 on Wednesday (afternoon) | Subject: debugging_approach | Observation: The user debugs by adding print statements rather than using a debugger | Confidence: 0.8",
            ),
        ],
    },
    "How does the user approach documentation?": {
        "semantic": [
            (
                0.5966,
                "Subject: documentation_habits | Type: behavior | Observation: The user always writes documentation before implementing features | Quote: I document the API design before writing any code",
            ),
            (
                0.5473,
                "The user always writes documentation before implementing features",
            ),
            (0.5207, "I document the API design before writing any code"),
            (
                0.4954,
                "Subject: debugging_approach | Type: behavior | Observation: The user debugs by adding print statements rather than using a debugger | Quote: When debugging, I just add console.log everywhere",
            ),
        ],
        "temporal": [
            (
                0.4988,
                "Time: 12:00 on Wednesday (afternoon) | Subject: documentation_habits | Observation: The user always writes documentation before implementing features | Confidence: 0.8",
            ),
            (
                0.4335,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces | Confidence: 0.8",
            ),
            (
                0.4316,
                "Time: 12:00 on Wednesday (afternoon) | Subject: debugging_approach | Observation: The user debugs by adding print statements rather than using a debugger | Confidence: 0.8",
            ),
            (
                0.4307,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI | Confidence: 0.8",
            ),
        ],
    },
    "What are the user's collaboration preferences?": {
        "semantic": [
            (
                0.651,
                "Subject: collaboration_preference | Type: preference | Observation: The user prefers pair programming for complex problems | Quote: Two heads are better than one when solving hard problems",
            ),
            (0.5848, "The user prefers pair programming for complex problems"),
            (
                0.5355,
                "Subject: version_control_style | Type: preference | Observation: The user prefers small, focused commits over large feature branches | Quote: I like to commit small, logical changes frequently",
            ),
            (
                0.5216,
                "Subject: domain_preference | Type: preference | Observation: The user prefers working on backend systems over frontend UI | Quote: I find backend logic more interesting than UI work",
            ),
        ],
        "temporal": [
            (
                0.6027,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems | Confidence: 0.8",
            ),
            (
                0.5101,
                "Time: 12:00 on Wednesday (afternoon) | Subject: version_control_style | Observation: The user prefers small, focused commits over large feature branches | Confidence: 0.8",
            ),
            (
                0.482,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI | Confidence: 0.8",
            ),
            (
                0.4782,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work | Confidence: 0.8",
            ),
        ],
    },
    "What does the user think about remote work?": {
        "semantic": [
            (0.7063, "The user thinks remote work is more productive than office work"),
            (
                0.6583,
                "Subject: work_environment | Type: belief | Observation: The user thinks remote work is more productive than office work | Quote: I get more done working from home",
            ),
            (0.6032, "I get more done working from home"),
            (0.4997, "The user prefers working on backend systems over frontend UI"),
        ],
        "temporal": [
            (
                0.5934,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work | Confidence: 0.8",
            ),
            (
                0.4173,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_schedule | Observation: The user prefers working late at night | Confidence: 0.8",
            ),
            (
                0.4148,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems | Confidence: 0.8",
            ),
            (
                0.4121,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes | Confidence: 0.8",
            ),
        ],
    },
    "What are the user's productivity methods?": {
        "semantic": [
            (
                0.5723,
                "Subject: productivity_methods | Type: behavior | Observation: The user takes breaks every 25 minutes using the Pomodoro technique | Quote: I use 25-minute work intervals with 5-minute breaks",
            ),
            (
                0.5261,
                "The user takes breaks every 25 minutes using the Pomodoro technique",
            ),
            (0.5205, "I use 25-minute work intervals with 5-minute breaks"),
            (0.5107, "The user thinks remote work is more productive than office work"),
        ],
        "temporal": [
            (
                0.5427,
                "Time: 12:00 on Wednesday (afternoon) | Subject: productivity_methods | Observation: The user takes breaks every 25 minutes using the Pomodoro technique | Confidence: 0.8",
            ),
            (
                0.4743,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work | Confidence: 0.8",
            ),
            (
                0.4299,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems | Confidence: 0.8",
            ),
            (
                0.4227,
                "Time: 12:00 on Wednesday (afternoon) | Subject: version_control_style | Observation: The user prefers small, focused commits over large feature branches | Confidence: 0.8",
            ),
        ],
    },
    "What technical skills is the user learning?": {
        "semantic": [
            (0.5765, "The user is currently learning Rust in their spare time"),
            (
                0.5502,
                "Subject: learning_activities | Type: general | Observation: The user is currently learning Rust in their spare time | Quote: I'm picking up Rust on weekends",
            ),
            (0.5411, "I'm picking up Rust on weekends"),
            (0.5155, "The user primarily works with Python and JavaScript"),
        ],
        "temporal": [
            (
                0.5301,
                "Time: 12:00 on Wednesday (afternoon) | Subject: learning_activities | Observation: The user is currently learning Rust in their spare time | Confidence: 0.8",
            ),
            (
                0.4913,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript | Confidence: 0.8",
            ),
            (
                0.481,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience | Confidence: 0.8",
            ),
            (
                0.4558,
                "Time: 12:00 on Wednesday (afternoon) | Subject: education_background | Observation: The user graduated with a Computer Science degree from Stanford | Confidence: 0.8",
            ),
        ],
    },
    "What does the user think about cooking?": {
        "semantic": [
            (0.4888, "I find backend logic more interesting than UI work"),
            (0.4624, "The user prefers working on backend systems over frontend UI"),
            (
                0.4551,
                "The user believes functional programming leads to better code quality",
            ),
            (0.4547, "The user said pure functions are yucky"),
        ],
        "temporal": [
            (
                0.3812,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky | Confidence: 0.8",
            ),
            (
                0.3773,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality | Confidence: 0.8",
            ),
            (
                0.3686,
                "Time: 12:00 on Wednesday (afternoon) | Subject: typescript_opinion | Observation: The user now says they love TypeScript but previously called it verbose | Confidence: 0.8",
            ),
            (
                0.3649,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI | Confidence: 0.8",
            ),
        ],
    },
    "What are the user's travel preferences?": {
        "semantic": [
            (
                0.522,
                "Subject: domain_preference | Type: preference | Observation: The user prefers working on backend systems over frontend UI | Quote: I find backend logic more interesting than UI work",
            ),
            (0.5145, "The user prefers functional programming over OOP"),
            (0.5079, "The user prefers working on backend systems over frontend UI"),
            (0.5045, "The user prefers working late at night"),
        ],
        "temporal": [
            (
                0.4849,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI | Confidence: 0.8",
            ),
            (
                0.4779,
                "Time: 12:00 on Wednesday (afternoon) | Subject: database_preference | Observation: The user prefers PostgreSQL over MongoDB for most applications | Confidence: 0.8",
            ),
            (
                0.4659,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems | Confidence: 0.8",
            ),
            (
                0.4639,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP | Confidence: 0.8",
            ),
        ],
    },
    "What music does the user like?": {
        "semantic": [
            (
                0.4927,
                "Subject: domain_preference | Type: preference | Observation: The user prefers working on backend systems over frontend UI | Quote: I find backend logic more interesting than UI work",
            ),
            (0.4906, "The user prefers working late at night"),
            (0.4904, "The user prefers functional programming over OOP"),
            (0.4894, "The user primarily works with Python and JavaScript"),
        ],
        "temporal": [
            (
                0.4674,
                "Time: 12:00 on Wednesday (afternoon) | Subject: typescript_opinion | Observation: The user now says they love TypeScript but previously called it verbose | Confidence: 0.8",
            ),
            (
                0.4548,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript | Confidence: 0.8",
            ),
            (
                0.4518,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP | Confidence: 0.8",
            ),
            (
                0.4496,
                "Time: 12:00 on Wednesday (afternoon) | Subject: editor_preference | Observation: The user prefers Vim over VS Code for editing | Confidence: 0.8",
            ),
        ],
    },
}


def test_real_observation_embeddings(real_voyage_client, qdrant):
    beliefs = [
        ("The user thinks that all men must die.", "All humans are mortal.", "humans"),
        (
            "The user believes functional programming leads to better code quality",
            "Functional programming produces more maintainable code",
            "programming_philosophy",
        ),
        (
            "The user thinks AI will replace most software developers within 10 years",
            "AI will make most programmers obsolete by 2035",
            "ai_future",
        ),
        (
            "The user believes code reviews are essential for quality",
            "Code reviews catch bugs that automated testing misses",
            "code_quality",
        ),
        (
            "The user thinks remote work is more productive than office work",
            "I get more done working from home",
            "work_environment",
        ),
        (
            "The user believes unit tests are a waste of time for prototypes",
            "Writing tests for throwaway code slows development",
            "testing_philosophy",
        ),
    ]

    behaviors = [
        (
            "The user always refactors to pure functions",
            "I always refactor to pure functions",
            "refactoring",
        ),
        (
            "The user writes commit messages in present tense",
            "Fix bug in parser instead of Fixed bug in parser",
            "git_habits",
        ),
        (
            "The user prefers working late at night",
            "I do my best coding between 10pm and 2am",
            "work_schedule",
        ),
        (
            "The user always writes documentation before implementing features",
            "I document the API design before writing any code",
            "documentation_habits",
        ),
        (
            "The user debugs by adding print statements rather than using a debugger",
            "When debugging, I just add console.log everywhere",
            "debugging_approach",
        ),
        (
            "The user takes breaks every 25 minutes using the Pomodoro technique",
            "I use 25-minute work intervals with 5-minute breaks",
            "productivity_methods",
        ),
    ]

    contradictions = [
        (
            "The user said pure functions are yucky",
            "Pure functions are yucky",
            "pure_functions",
        ),
        (
            "The user now says they love TypeScript but previously called it verbose",
            "TypeScript has too much boilerplate vs TypeScript makes my code so much cleaner",
            "typescript_opinion",
        ),
        (
            "The user claims to prefer tabs but their code uses spaces",
            "Tabs are better than spaces vs code consistently uses 2-space indentation",
            "indentation_preference",
        ),
    ]

    preferences = [
        (
            "The user prefers functional programming over OOP",
            "I prefer functional programming over OOP",
            "programming_paradigms",
        ),
        (
            "The user prefers Vim over VS Code for editing",
            "Vim makes me more productive than any modern editor",
            "editor_preference",
        ),
        (
            "The user prefers working on backend systems over frontend UI",
            "I find backend logic more interesting than UI work",
            "domain_preference",
        ),
        (
            "The user prefers small, focused commits over large feature branches",
            "I like to commit small, logical changes frequently",
            "version_control_style",
        ),
        (
            "The user prefers PostgreSQL over MongoDB for most applications",
            "Relational databases handle complex queries better than document stores",
            "database_preference",
        ),
        (
            "The user prefers pair programming for complex problems",
            "Two heads are better than one when solving hard problems",
            "collaboration_preference",
        ),
    ]

    general = [
        ("The user is a human", "The user is a human", "humans"),
        (
            "The user has 8 years of professional programming experience",
            "I've been coding professionally for 8 years",
            "experience_level",
        ),
        (
            "The user primarily works with Python and JavaScript",
            "Most of my work is in Python backend and React frontend",
            "primary_languages",
        ),
        (
            "The user works at a mid-size startup with 50 employees",
            "Our company has about 50 people",
            "company_size",
        ),
        (
            "The user graduated with a Computer Science degree from Stanford",
            "I studied CS at Stanford",
            "education_background",
        ),
        (
            "The user is currently learning Rust in their spare time",
            "I'm picking up Rust on weekends",
            "learning_activities",
        ),
    ]

    ids = itertools.count(1)
    items = [
        AgentObservation(
            id=next(ids),
            content=content,
            mime_type="text/html",
            modality="observation",
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            size=len(content),
            tags=["bla"],
            observation_type=observation_type,
            subject=subject,
            confidence=0.8,
            evidence={
                "quote": quote,
                "source": "https://en.wikipedia.org/wiki/Human",
            },
            agent_model="gpt-4o",
            inserted_at=datetime(2025, 1, 1, 12, 0, 0),
            embed_status="QUEUED",
        )
        for observation_type, observations in [
            ("belief", beliefs),
            ("behavior", behaviors),
            ("contradiction", contradictions),
            ("preference", preferences),
            ("general", general),
        ]
        for content, quote, subject in observations
    ]

    for item in items:
        embed_source_item(item)
    push_to_qdrant(items)

    chunk_map = {str(c.id): c for item in items for c in item.chunks}

    def get_top(vector, search_type: str) -> list[tuple[float, str]]:
        results = qdrant_tools.search_vectors(qdrant, search_type, vector)
        return [
            (round(i.score, 4), chunk_map[str(i.id)].content)
            for i in sorted(results, key=lambda x: x.score, reverse=True)
        ][:4]

    for query, expected in EXPECTED_OBSERVATION_RESULTS.items():
        search_vector = embed_text(
            [extract.DataChunk(data=[query])], input_type="query"
        )[0]
        semantic_results = get_top(search_vector, "semantic")
        temporal_results = get_top(search_vector, "temporal")
        assert semantic_results == expected["semantic"]
        assert temporal_results == expected["temporal"]

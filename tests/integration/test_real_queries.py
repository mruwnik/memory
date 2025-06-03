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
            (0.6788, "I prefer functional programming over OOP"),
            (
                0.6759,
                "Subject: programming_philosophy | Type: belief | Observation: The user believes functional programming leads to better code quality | Quote: Functional programming produces more maintainable code",
            ),
            (
                0.6678,
                "Subject: programming_paradigms | Type: preference | Observation: The user prefers functional programming over OOP | Quote: I prefer functional programming over OOP",
            ),
        ],
        "temporal": [
            (
                0.5971,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.5308,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP",
            ),
            (
                0.5167,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky",
            ),
            (
                0.4702,
                "Time: 12:00 on Wednesday (afternoon) | Subject: refactoring | Observation: The user always refactors to pure functions",
            ),
        ],
    },
    "Does the user prefer functional or object-oriented programming?": {
        "semantic": [
            (0.7719, "The user prefers functional programming over OOP"),
            (
                0.7541,
                "Subject: programming_paradigms | Type: preference | Observation: The user prefers functional programming over OOP | Quote: I prefer functional programming over OOP",
            ),
            (0.7455, "I prefer functional programming over OOP"),
            (
                0.6536,
                "The user believes functional programming leads to better code quality",
            ),
        ],
        "temporal": [
            (
                0.6251,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP",
            ),
            (
                0.6062,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.5061,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky",
            ),
            (
                0.5036,
                "Time: 12:00 on Wednesday (afternoon) | Subject: refactoring | Observation: The user always refactors to pure functions",
            ),
        ],
    },
    "What are the user's beliefs about code quality?": {
        "semantic": [
            (0.6925, "The user believes code reviews are essential for quality"),
            (
                0.6801,
                "The user believes functional programming leads to better code quality",
            ),
            (
                0.6525,
                "Subject: code_quality | Type: belief | Observation: The user believes code reviews are essential for quality | Quote: Code reviews catch bugs that automated testing misses",
            ),
            (
                0.6471,
                "Subject: programming_philosophy | Type: belief | Observation: The user believes functional programming leads to better code quality | Quote: Functional programming produces more maintainable code",
            ),
        ],
        "temporal": [
            (
                0.5269,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.5193,
                "Time: 12:00 on Wednesday (afternoon) | Subject: code_quality | Observation: The user believes code reviews are essential for quality",
            ),
            (
                0.468,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes",
            ),
            (
                0.4377,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky",
            ),
        ],
    },
    "How does the user approach debugging code?": {
        "semantic": [
            (
                0.7007,
                "Subject: debugging_approach | Type: behavior | Observation: The user debugs by adding print statements rather than using a debugger | Quote: When debugging, I just add console.log everywhere",
            ),
            (
                0.6956,
                "The user debugs by adding print statements rather than using a debugger",
            ),
            (0.6795, "When debugging, I just add console.log everywhere"),
            (
                0.5352,
                "Subject: code_quality | Type: belief | Observation: The user believes code reviews are essential for quality | Quote: Code reviews catch bugs that automated testing misses",
            ),
        ],
        "temporal": [
            (
                0.6253,
                "Time: 12:00 on Wednesday (afternoon) | Subject: debugging_approach | Observation: The user debugs by adding print statements rather than using a debugger",
            ),
            (
                0.48,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces",
            ),
            (
                0.4589,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes",
            ),
            (
                0.4502,
                "Time: 12:00 on Wednesday (afternoon) | Subject: version_control_style | Observation: The user prefers small, focused commits over large feature branches",
            ),
        ],
    },
    "What are the user's git and version control habits?": {
        "semantic": [
            (
                0.6485,
                "Subject: version_control_style | Type: preference | Observation: The user prefers small, focused commits over large feature branches | Quote: I like to commit small, logical changes frequently",
            ),
            (0.643, "I like to commit small, logical changes frequently"),
            (
                0.5968,
                "The user prefers small, focused commits over large feature branches",
            ),
            (
                0.5813,
                "Subject: git_habits | Type: behavior | Observation: The user writes commit messages in present tense | Quote: Fix bug in parser instead of Fixed bug in parser",
            ),
        ],
        "temporal": [
            (
                0.6063,
                "Time: 12:00 on Wednesday (afternoon) | Subject: version_control_style | Observation: The user prefers small, focused commits over large feature branches",
            ),
            (
                0.5569,
                "Time: 12:00 on Wednesday (afternoon) | Subject: git_habits | Observation: The user writes commit messages in present tense",
            ),
            (
                0.4806,
                "Time: 12:00 on Wednesday (afternoon) | Subject: editor_preference | Observation: The user prefers Vim over VS Code for editing",
            ),
            (
                0.4622,
                "Time: 12:00 on Wednesday (afternoon) | Subject: code_quality | Observation: The user believes code reviews are essential for quality",
            ),
        ],
    },
    "When does the user prefer to work?": {
        "semantic": [
            (0.6805, "The user prefers working late at night"),
            (
                0.6794,
                "Subject: work_schedule | Type: behavior | Observation: The user prefers working late at night | Quote: I do my best coding between 10pm and 2am",
            ),
            (0.6432, "I do my best coding between 10pm and 2am"),
            (0.5525, "I use 25-minute work intervals with 5-minute breaks"),
        ],
        "temporal": [
            (
                0.6896,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_schedule | Observation: The user prefers working late at night",
            ),
            (
                0.6327,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
            (
                0.6266,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work",
            ),
            (
                0.6206,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems",
            ),
        ],
    },
    "How does the user handle productivity and time management?": {
        "semantic": [
            (
                0.5795,
                "Subject: productivity_methods | Type: behavior | Observation: The user takes breaks every 25 minutes using the Pomodoro technique | Quote: I use 25-minute work intervals with 5-minute breaks",
            ),
            (0.5727, "I use 25-minute work intervals with 5-minute breaks"),
            (
                0.5282,
                "The user takes breaks every 25 minutes using the Pomodoro technique",
            ),
            (0.515, "I do my best coding between 10pm and 2am"),
        ],
        "temporal": [
            (
                0.5633,
                "Time: 12:00 on Wednesday (afternoon) | Subject: productivity_methods | Observation: The user takes breaks every 25 minutes using the Pomodoro technique",
            ),
            (
                0.5105,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work",
            ),
            (
                0.4737,
                "Time: 12:00 on Wednesday (afternoon) | Subject: documentation_habits | Observation: The user always writes documentation before implementing features",
            ),
            (
                0.4672,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_schedule | Observation: The user prefers working late at night",
            ),
        ],
    },
    "What editor does the user prefer?": {
        "semantic": [
            (
                0.6398,
                "Subject: editor_preference | Type: preference | Observation: The user prefers Vim over VS Code for editing | Quote: Vim makes me more productive than any modern editor",
            ),
            (0.6242, "The user prefers Vim over VS Code for editing"),
            (0.5524, "Vim makes me more productive than any modern editor"),
            (0.4887, "The user claims to prefer tabs but their code uses spaces"),
        ],
        "temporal": [
            (
                0.5626,
                "Time: 12:00 on Wednesday (afternoon) | Subject: editor_preference | Observation: The user prefers Vim over VS Code for editing",
            ),
            (
                0.4507,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces",
            ),
            (
                0.4333,
                "Time: 12:00 on Wednesday (afternoon) | Subject: database_preference | Observation: The user prefers PostgreSQL over MongoDB for most applications",
            ),
            (
                0.4307,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
        ],
    },
    "What databases does the user like to use?": {
        "semantic": [
            (
                0.6328,
                "Subject: database_preference | Type: preference | Observation: The user prefers PostgreSQL over MongoDB for most applications | Quote: Relational databases handle complex queries better than document stores",
            ),
            (0.5991, "The user prefers PostgreSQL over MongoDB for most applications"),
            (
                0.5357,
                "Subject: domain_preference | Type: preference | Observation: The user prefers working on backend systems over frontend UI | Quote: I find backend logic more interesting than UI work",
            ),
            (0.5178, "The user prefers working on backend systems over frontend UI"),
        ],
        "temporal": [
            (
                0.5503,
                "Time: 12:00 on Wednesday (afternoon) | Subject: database_preference | Observation: The user prefers PostgreSQL over MongoDB for most applications",
            ),
            (
                0.4583,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
            (
                0.4445,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript",
            ),
            (
                0.427,
                "Time: 12:00 on Wednesday (afternoon) | Subject: editor_preference | Observation: The user prefers Vim over VS Code for editing",
            ),
        ],
    },
    "What programming languages does the user work with?": {
        "semantic": [
            (0.7264, "The user primarily works with Python and JavaScript"),
            (0.6958, "Most of my work is in Python backend and React frontend"),
            (
                0.6875,
                "Subject: primary_languages | Type: general | Observation: The user primarily works with Python and JavaScript | Quote: Most of my work is in Python backend and React frontend",
            ),
            (0.6111, "I'm picking up Rust on weekends"),
        ],
        "temporal": [
            (
                0.5774,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript",
            ),
            (
                0.4692,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience",
            ),
            (
                0.454,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.4475,
                "Time: 12:00 on Wednesday (afternoon) | Subject: learning_activities | Observation: The user is currently learning Rust in their spare time",
            ),
        ],
    },
    "What is the user's programming experience level?": {
        "semantic": [
            (0.6663, "The user has 8 years of professional programming experience"),
            (
                0.6562,
                "Subject: experience_level | Type: general | Observation: The user has 8 years of professional programming experience | Quote: I've been coding professionally for 8 years",
            ),
            (0.5952, "I've been coding professionally for 8 years"),
            (0.5656, "The user is currently learning Rust in their spare time"),
        ],
        "temporal": [
            (
                0.5808,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience",
            ),
            (
                0.4814,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript",
            ),
            (
                0.4752,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.4591,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP",
            ),
        ],
    },
    "Where did the user study computer science?": {
        "semantic": [
            (0.686, "I studied CS at Stanford"),
            (0.6484, "The user graduated with a Computer Science degree from Stanford"),
            (
                0.6346,
                "Subject: education_background | Type: general | Observation: The user graduated with a Computer Science degree from Stanford | Quote: I studied CS at Stanford",
            ),
            (0.4599, "The user is currently learning Rust in their spare time"),
        ],
        "temporal": [
            (
                0.5288,
                "Time: 12:00 on Wednesday (afternoon) | Subject: education_background | Observation: The user graduated with a Computer Science degree from Stanford",
            ),
            (
                0.3833,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience",
            ),
            (
                0.3728,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript",
            ),
            (
                0.3651,
                "Time: 12:00 on Wednesday (afternoon) | Subject: learning_activities | Observation: The user is currently learning Rust in their spare time",
            ),
        ],
    },
    "What kind of company does the user work at?": {
        "semantic": [
            (0.6304, "The user works at a mid-size startup with 50 employees"),
            (
                0.5369,
                "Subject: company_size | Type: general | Observation: The user works at a mid-size startup with 50 employees | Quote: Our company has about 50 people",
            ),
            (0.5258, "Most of my work is in Python backend and React frontend"),
            (0.4905, "I've been coding professionally for 8 years"),
        ],
        "temporal": [
            (
                0.5194,
                "Time: 12:00 on Wednesday (afternoon) | Subject: company_size | Observation: The user works at a mid-size startup with 50 employees",
            ),
            (
                0.4149,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work",
            ),
            (
                0.4144,
                "Time: 12:00 on Wednesday (afternoon) | Subject: education_background | Observation: The user graduated with a Computer Science degree from Stanford",
            ),
            (
                0.4053,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience",
            ),
        ],
    },
    "What does the user think about AI replacing programmers?": {
        "semantic": [
            (
                0.5955,
                "Subject: ai_future | Type: belief | Observation: The user thinks AI will replace most software developers within 10 years | Quote: AI will make most programmers obsolete by 2035",
            ),
            (0.5725, "AI will make most programmers obsolete by 2035"),
            (
                0.572,
                "The user thinks AI will replace most software developers within 10 years",
            ),
            (
                0.4342,
                "The user believes functional programming leads to better code quality",
            ),
        ],
        "temporal": [
            (
                0.4546,
                "Time: 12:00 on Wednesday (afternoon) | Subject: ai_future | Observation: The user thinks AI will replace most software developers within 10 years",
            ),
            (
                0.3583,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.3264,
                "Time: 12:00 on Wednesday (afternoon) | Subject: typescript_opinion | Observation: The user now says they love TypeScript but previously called it verbose",
            ),
            (
                0.3257,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes",
            ),
        ],
    },
    "What are the user's views on artificial intelligence?": {
        "semantic": [
            (
                0.5884,
                "Subject: ai_future | Type: belief | Observation: The user thinks AI will replace most software developers within 10 years | Quote: AI will make most programmers obsolete by 2035",
            ),
            (
                0.5659,
                "The user thinks AI will replace most software developers within 10 years",
            ),
            (0.5139, "AI will make most programmers obsolete by 2035"),
            (0.4927, "I find backend logic more interesting than UI work"),
        ],
        "temporal": [
            (
                0.5205,
                "Time: 12:00 on Wednesday (afternoon) | Subject: ai_future | Observation: The user thinks AI will replace most software developers within 10 years",
            ),
            (
                0.4203,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.4007,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky",
            ),
            (
                0.4001,
                "Time: 12:00 on Wednesday (afternoon) | Subject: humans | Observation: The user thinks that all men must die.",
            ),
        ],
    },
    "Has the user changed their mind about TypeScript?": {
        "semantic": [
            (
                0.6166,
                "The user now says they love TypeScript but previously called it verbose",
            ),
            (
                0.5764,
                "Subject: typescript_opinion | Type: contradiction | Observation: The user now says they love TypeScript but previously called it verbose | Quote: TypeScript has too much boilerplate vs TypeScript makes my code so much cleaner",
            ),
            (
                0.4907,
                "TypeScript has too much boilerplate vs TypeScript makes my code so much cleaner",
            ),
            (0.4159, "The user always refactors to pure functions"),
        ],
        "temporal": [
            (
                0.5663,
                "Time: 12:00 on Wednesday (afternoon) | Subject: typescript_opinion | Observation: The user now says they love TypeScript but previously called it verbose",
            ),
            (
                0.3897,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces",
            ),
            (
                0.3833,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript",
            ),
            (
                0.3761,
                "Time: 12:00 on Wednesday (afternoon) | Subject: editor_preference | Observation: The user prefers Vim over VS Code for editing",
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
                0.5328,
                "Subject: pure_functions | Type: contradiction | Observation: The user said pure functions are yucky | Quote: Pure functions are yucky",
            ),
            (
                0.507,
                "Subject: typescript_opinion | Type: contradiction | Observation: The user now says they love TypeScript but previously called it verbose | Quote: TypeScript has too much boilerplate vs TypeScript makes my code so much cleaner",
            ),
        ],
        "temporal": [
            (
                0.4671,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
            (
                0.4661,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces",
            ),
            (
                0.4566,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky",
            ),
            (
                0.4553,
                "Time: 12:00 on Wednesday (afternoon) | Subject: database_preference | Observation: The user prefers PostgreSQL over MongoDB for most applications",
            ),
        ],
    },
    "What does the user think about software testing?": {
        "semantic": [
            (
                0.6384,
                "Subject: testing_philosophy | Type: belief | Observation: The user believes unit tests are a waste of time for prototypes | Quote: Writing tests for throwaway code slows development",
            ),
            (0.6219, "The user believes unit tests are a waste of time for prototypes"),
            (
                0.6154,
                "Subject: code_quality | Type: belief | Observation: The user believes code reviews are essential for quality | Quote: Code reviews catch bugs that automated testing misses",
            ),
            (0.6031, "The user believes code reviews are essential for quality"),
        ],
        "temporal": [
            (
                0.568,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes",
            ),
            (
                0.4901,
                "Time: 12:00 on Wednesday (afternoon) | Subject: code_quality | Observation: The user believes code reviews are essential for quality",
            ),
            (
                0.4745,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.4524,
                "Time: 12:00 on Wednesday (afternoon) | Subject: debugging_approach | Observation: The user debugs by adding print statements rather than using a debugger",
            ),
        ],
    },
    "How does the user approach documentation?": {
        "semantic": [
            (
                0.597,
                "Subject: documentation_habits | Type: behavior | Observation: The user always writes documentation before implementing features | Quote: I document the API design before writing any code",
            ),
            (
                0.5462,
                "The user always writes documentation before implementing features",
            ),
            (0.5213, "I document the API design before writing any code"),
            (
                0.4949,
                "Subject: debugging_approach | Type: behavior | Observation: The user debugs by adding print statements rather than using a debugger | Quote: When debugging, I just add console.log everywhere",
            ),
        ],
        "temporal": [
            (
                0.5001,
                "Time: 12:00 on Wednesday (afternoon) | Subject: documentation_habits | Observation: The user always writes documentation before implementing features",
            ),
            (
                0.4371,
                "Time: 12:00 on Wednesday (afternoon) | Subject: version_control_style | Observation: The user prefers small, focused commits over large feature branches",
            ),
            (
                0.4355,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces",
            ),
            (
                0.4347,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
        ],
    },
    "What are the user's collaboration preferences?": {
        "semantic": [
            (
                0.6516,
                "Subject: collaboration_preference | Type: preference | Observation: The user prefers pair programming for complex problems | Quote: Two heads are better than one when solving hard problems",
            ),
            (0.5855, "The user prefers pair programming for complex problems"),
            (
                0.5361,
                "Subject: version_control_style | Type: preference | Observation: The user prefers small, focused commits over large feature branches | Quote: I like to commit small, logical changes frequently",
            ),
            (
                0.522,
                "Subject: domain_preference | Type: preference | Observation: The user prefers working on backend systems over frontend UI | Quote: I find backend logic more interesting than UI work",
            ),
        ],
        "temporal": [
            (
                0.5889,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems",
            ),
            (
                0.502,
                "Time: 12:00 on Wednesday (afternoon) | Subject: version_control_style | Observation: The user prefers small, focused commits over large feature branches",
            ),
            (
                0.4754,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
            (
                0.4638,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work",
            ),
        ],
    },
    "What does the user think about remote work?": {
        "semantic": [
            (0.7054, "The user thinks remote work is more productive than office work"),
            (
                0.6581,
                "Subject: work_environment | Type: belief | Observation: The user thinks remote work is more productive than office work | Quote: I get more done working from home",
            ),
            (0.6026, "I get more done working from home"),
            (0.4991, "The user prefers working on backend systems over frontend UI"),
        ],
        "temporal": [
            (
                0.5832,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work",
            ),
            (
                0.4126,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes",
            ),
            (
                0.4122,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems",
            ),
            (
                0.4092,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
        ],
    },
    "What are the user's productivity methods?": {
        "semantic": [
            (
                0.5729,
                "Subject: productivity_methods | Type: behavior | Observation: The user takes breaks every 25 minutes using the Pomodoro technique | Quote: I use 25-minute work intervals with 5-minute breaks",
            ),
            (
                0.5261,
                "The user takes breaks every 25 minutes using the Pomodoro technique",
            ),
            (0.5205, "I use 25-minute work intervals with 5-minute breaks"),
            (0.512, "The user thinks remote work is more productive than office work"),
        ],
        "temporal": [
            (
                0.5312,
                "Time: 12:00 on Wednesday (afternoon) | Subject: productivity_methods | Observation: The user takes breaks every 25 minutes using the Pomodoro technique",
            ),
            (
                0.4796,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work",
            ),
            (
                0.4344,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems",
            ),
            (
                0.429,
                "Time: 12:00 on Wednesday (afternoon) | Subject: refactoring | Observation: The user always refactors to pure functions",
            ),
        ],
    },
    "What technical skills is the user learning?": {
        "semantic": [
            (0.5766, "The user is currently learning Rust in their spare time"),
            (
                0.55,
                "Subject: learning_activities | Type: general | Observation: The user is currently learning Rust in their spare time | Quote: I'm picking up Rust on weekends",
            ),
            (0.5415, "I'm picking up Rust on weekends"),
            (0.5156, "The user primarily works with Python and JavaScript"),
        ],
        "temporal": [
            (
                0.5221,
                "Time: 12:00 on Wednesday (afternoon) | Subject: learning_activities | Observation: The user is currently learning Rust in their spare time",
            ),
            (
                0.492,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript",
            ),
            (
                0.4871,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience",
            ),
            (
                0.4547,
                "Time: 12:00 on Wednesday (afternoon) | Subject: education_background | Observation: The user graduated with a Computer Science degree from Stanford",
            ),
        ],
    },
    "What does the user think about cooking?": {
        "semantic": [
            (0.4893, "I find backend logic more interesting than UI work"),
            (0.4621, "The user prefers working on backend systems over frontend UI"),
            (
                0.4551,
                "The user believes functional programming leads to better code quality",
            ),
            (0.4549, "The user said pure functions are yucky"),
        ],
        "temporal": [
            (
                0.3785,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky",
            ),
            (
                0.3759,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.375,
                "Time: 12:00 on Wednesday (afternoon) | Subject: typescript_opinion | Observation: The user now says they love TypeScript but previously called it verbose",
            ),
            (
                0.3594,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
        ],
    },
    "What are the user's travel preferences?": {
        "semantic": [
            (
                0.523,
                "Subject: domain_preference | Type: preference | Observation: The user prefers working on backend systems over frontend UI | Quote: I find backend logic more interesting than UI work",
            ),
            (0.5143, "The user prefers functional programming over OOP"),
            (0.5074, "The user prefers working on backend systems over frontend UI"),
            (0.5049, "The user prefers working late at night"),
        ],
        "temporal": [
            (
                0.4767,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
            (
                0.4748,
                "Time: 12:00 on Wednesday (afternoon) | Subject: database_preference | Observation: The user prefers PostgreSQL over MongoDB for most applications",
            ),
            (
                0.4587,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP",
            ),
            (
                0.4554,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems",
            ),
        ],
    },
    "What music does the user like?": {
        "semantic": [
            (
                0.4933,
                "Subject: domain_preference | Type: preference | Observation: The user prefers working on backend systems over frontend UI | Quote: I find backend logic more interesting than UI work",
            ),
            (0.4906, "The user prefers working late at night"),
            (0.4902, "The user prefers functional programming over OOP"),
            (0.4894, "The user primarily works with Python and JavaScript"),
        ],
        "temporal": [
            (
                0.4676,
                "Time: 12:00 on Wednesday (afternoon) | Subject: typescript_opinion | Observation: The user now says they love TypeScript but previously called it verbose",
            ),
            (
                0.4561,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript",
            ),
            (
                0.4471,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP",
            ),
            (
                0.4432,
                "Time: 12:00 on Wednesday (afternoon) | Subject: editor_preference | Observation: The user prefers Vim over VS Code for editing",
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
        item.update_confidences({"observation_accuracy": 0.8})
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

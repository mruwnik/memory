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
                0.71,
                "The user believes functional programming leads to better code quality",
            ),
            (0.679, "I prefer functional programming over OOP"),
            (
                0.676,
                "Subject: programming_philosophy | Type: belief | Observation: The user believes functional programming leads to better code quality | Quote: Functional programming produces more maintainable code",
            ),
            (
                0.668,
                "Subject: programming_paradigms | Type: preference | Observation: The user prefers functional programming over OOP | Quote: I prefer functional programming over OOP",
            ),
        ],
        "temporal": [
            (
                0.597,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.531,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP",
            ),
            (
                0.517,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky",
            ),
            (
                0.47,
                "Time: 12:00 on Wednesday (afternoon) | Subject: refactoring | Observation: The user always refactors to pure functions",
            ),
        ],
    },
    "Does the user prefer functional or object-oriented programming?": {
        "semantic": [
            (0.772, "The user prefers functional programming over OOP"),
            (
                0.754,
                "Subject: programming_paradigms | Type: preference | Observation: The user prefers functional programming over OOP | Quote: I prefer functional programming over OOP",
            ),
            (0.745, "I prefer functional programming over OOP"),
            (
                0.654,
                "The user believes functional programming leads to better code quality",
            ),
        ],
        "temporal": [
            (
                0.625,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP",
            ),
            (
                0.606,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.506,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky",
            ),
            (
                0.504,
                "Time: 12:00 on Wednesday (afternoon) | Subject: refactoring | Observation: The user always refactors to pure functions",
            ),
        ],
    },
    "What are the user's beliefs about code quality?": {
        "semantic": [
            (0.692, "The user believes code reviews are essential for quality"),
            (
                0.68,
                "The user believes functional programming leads to better code quality",
            ),
            (
                0.652,
                "Subject: code_quality | Type: belief | Observation: The user believes code reviews are essential for quality | Quote: Code reviews catch bugs that automated testing misses",
            ),
            (
                0.647,
                "Subject: programming_philosophy | Type: belief | Observation: The user believes functional programming leads to better code quality | Quote: Functional programming produces more maintainable code",
            ),
        ],
        "temporal": [
            (
                0.527,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.519,
                "Time: 12:00 on Wednesday (afternoon) | Subject: code_quality | Observation: The user believes code reviews are essential for quality",
            ),
            (
                0.468,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes",
            ),
            (
                0.438,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky",
            ),
        ],
    },
    "How does the user approach debugging code?": {
        "semantic": [
            (
                0.701,
                "Subject: debugging_approach | Type: behavior | Observation: The user debugs by adding print statements rather than using a debugger | Quote: When debugging, I just add console.log everywhere",
            ),
            (
                0.696,
                "The user debugs by adding print statements rather than using a debugger",
            ),
            (0.68, "When debugging, I just add console.log everywhere"),
            (
                0.535,
                "Subject: code_quality | Type: belief | Observation: The user believes code reviews are essential for quality | Quote: Code reviews catch bugs that automated testing misses",
            ),
        ],
        "temporal": [
            (
                0.625,
                "Time: 12:00 on Wednesday (afternoon) | Subject: debugging_approach | Observation: The user debugs by adding print statements rather than using a debugger",
            ),
            (
                0.48,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces",
            ),
            (
                0.459,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes",
            ),
            (
                0.45,
                "Time: 12:00 on Wednesday (afternoon) | Subject: version_control_style | Observation: The user prefers small, focused commits over large feature branches",
            ),
        ],
    },
    "What are the user's git and version control habits?": {
        "semantic": [
            (
                0.648,
                "Subject: version_control_style | Type: preference | Observation: The user prefers small, focused commits over large feature branches | Quote: I like to commit small, logical changes frequently",
            ),
            (0.643, "I like to commit small, logical changes frequently"),
            (
                0.597,
                "The user prefers small, focused commits over large feature branches",
            ),
            (
                0.581,
                "Subject: git_habits | Type: behavior | Observation: The user writes commit messages in present tense | Quote: Fix bug in parser instead of Fixed bug in parser",
            ),
        ],
        "temporal": [
            (
                0.606,
                "Time: 12:00 on Wednesday (afternoon) | Subject: version_control_style | Observation: The user prefers small, focused commits over large feature branches",
            ),
            (
                0.557,
                "Time: 12:00 on Wednesday (afternoon) | Subject: git_habits | Observation: The user writes commit messages in present tense",
            ),
            (
                0.481,
                "Time: 12:00 on Wednesday (afternoon) | Subject: editor_preference | Observation: The user prefers Vim over VS Code for editing",
            ),
            (
                0.462,
                "Time: 12:00 on Wednesday (afternoon) | Subject: code_quality | Observation: The user believes code reviews are essential for quality",
            ),
        ],
    },
    "When does the user prefer to work?": {
        "semantic": [
            (0.681, "The user prefers working late at night"),
            (
                0.679,
                "Subject: work_schedule | Type: behavior | Observation: The user prefers working late at night | Quote: I do my best coding between 10pm and 2am",
            ),
            (0.643, "I do my best coding between 10pm and 2am"),
            (0.553, "I use 25-minute work intervals with 5-minute breaks"),
        ],
        "temporal": [
            (
                0.69,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_schedule | Observation: The user prefers working late at night",
            ),
            (
                0.633,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
            (
                0.627,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work",
            ),
            (
                0.621,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems",
            ),
        ],
    },
    "How does the user handle productivity and time management?": {
        "semantic": [
            (
                0.579,
                "Subject: productivity_methods | Type: behavior | Observation: The user takes breaks every 25 minutes using the Pomodoro technique | Quote: I use 25-minute work intervals with 5-minute breaks",
            ),
            (0.572, "I use 25-minute work intervals with 5-minute breaks"),
            (
                0.527,
                "The user takes breaks every 25 minutes using the Pomodoro technique",
            ),
            (0.515, "I do my best coding between 10pm and 2am"),
        ],
        "temporal": [
            (
                0.563,
                "Time: 12:00 on Wednesday (afternoon) | Subject: productivity_methods | Observation: The user takes breaks every 25 minutes using the Pomodoro technique",
            ),
            (
                0.51,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work",
            ),
            (
                0.473,
                "Time: 12:00 on Wednesday (afternoon) | Subject: documentation_habits | Observation: The user always writes documentation before implementing features",
            ),
            (
                0.467,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_schedule | Observation: The user prefers working late at night",
            ),
        ],
    },
    "What editor does the user prefer?": {
        "semantic": [
            (
                0.64,
                "Subject: editor_preference | Type: preference | Observation: The user prefers Vim over VS Code for editing | Quote: Vim makes me more productive than any modern editor",
            ),
            (0.624, "The user prefers Vim over VS Code for editing"),
            (0.552, "Vim makes me more productive than any modern editor"),
            (0.489, "The user claims to prefer tabs but their code uses spaces"),
        ],
        "temporal": [
            (
                0.563,
                "Time: 12:00 on Wednesday (afternoon) | Subject: editor_preference | Observation: The user prefers Vim over VS Code for editing",
            ),
            (
                0.451,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces",
            ),
            (
                0.433,
                "Time: 12:00 on Wednesday (afternoon) | Subject: database_preference | Observation: The user prefers PostgreSQL over MongoDB for most applications",
            ),
            (
                0.431,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
        ],
    },
    "What databases does the user like to use?": {
        "semantic": [
            (
                0.633,
                "Subject: database_preference | Type: preference | Observation: The user prefers PostgreSQL over MongoDB for most applications | Quote: Relational databases handle complex queries better than document stores",
            ),
            (0.599, "The user prefers PostgreSQL over MongoDB for most applications"),
            (
                0.536,
                "Subject: domain_preference | Type: preference | Observation: The user prefers working on backend systems over frontend UI | Quote: I find backend logic more interesting than UI work",
            ),
            (0.518, "The user prefers working on backend systems over frontend UI"),
        ],
        "temporal": [
            (
                0.55,
                "Time: 12:00 on Wednesday (afternoon) | Subject: database_preference | Observation: The user prefers PostgreSQL over MongoDB for most applications",
            ),
            (
                0.458,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
            (
                0.445,
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
            (0.726, "The user primarily works with Python and JavaScript"),
            (0.696, "Most of my work is in Python backend and React frontend"),
            (
                0.688,
                "Subject: primary_languages | Type: general | Observation: The user primarily works with Python and JavaScript | Quote: Most of my work is in Python backend and React frontend",
            ),
            (0.611, "I'm picking up Rust on weekends"),
        ],
        "temporal": [
            (
                0.577,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript",
            ),
            (
                0.469,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience",
            ),
            (
                0.454,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.447,
                "Time: 12:00 on Wednesday (afternoon) | Subject: learning_activities | Observation: The user is currently learning Rust in their spare time",
            ),
        ],
    },
    "What is the user's programming experience level?": {
        "semantic": [
            (0.666, "The user has 8 years of professional programming experience"),
            (
                0.656,
                "Subject: experience_level | Type: general | Observation: The user has 8 years of professional programming experience | Quote: I've been coding professionally for 8 years",
            ),
            (0.595, "I've been coding professionally for 8 years"),
            (0.566, "The user is currently learning Rust in their spare time"),
        ],
        "temporal": [
            (
                0.581,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience",
            ),
            (
                0.481,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript",
            ),
            (
                0.475,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.459,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP",
            ),
        ],
    },
    "Where did the user study computer science?": {
        "semantic": [
            (0.686, "I studied CS at Stanford"),
            (0.648, "The user graduated with a Computer Science degree from Stanford"),
            (
                0.635,
                "Subject: education_background | Type: general | Observation: The user graduated with a Computer Science degree from Stanford | Quote: I studied CS at Stanford",
            ),
            (0.46, "The user is currently learning Rust in their spare time"),
        ],
        "temporal": [
            (
                0.529,
                "Time: 12:00 on Wednesday (afternoon) | Subject: education_background | Observation: The user graduated with a Computer Science degree from Stanford",
            ),
            (
                0.383,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience",
            ),
            (
                0.373,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript",
            ),
            (
                0.365,
                "Time: 12:00 on Wednesday (afternoon) | Subject: learning_activities | Observation: The user is currently learning Rust in their spare time",
            ),
        ],
    },
    "What kind of company does the user work at?": {
        "semantic": [
            (0.63, "The user works at a mid-size startup with 50 employees"),
            (
                0.537,
                "Subject: company_size | Type: general | Observation: The user works at a mid-size startup with 50 employees | Quote: Our company has about 50 people",
            ),
            (0.526, "Most of my work is in Python backend and React frontend"),
            (0.49, "I've been coding professionally for 8 years"),
        ],
        "temporal": [
            (
                0.519,
                "Time: 12:00 on Wednesday (afternoon) | Subject: company_size | Observation: The user works at a mid-size startup with 50 employees",
            ),
            (
                0.415,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work",
            ),
            (
                0.414,
                "Time: 12:00 on Wednesday (afternoon) | Subject: education_background | Observation: The user graduated with a Computer Science degree from Stanford",
            ),
            (
                0.405,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience",
            ),
        ],
    },
    "What does the user think about AI replacing programmers?": {
        "semantic": [
            (
                0.596,
                "Subject: ai_future | Type: belief | Observation: The user thinks AI will replace most software developers within 10 years | Quote: AI will make most programmers obsolete by 2035",
            ),
            (0.572, "AI will make most programmers obsolete by 2035"),
            (
                0.572,
                "The user thinks AI will replace most software developers within 10 years",
            ),
            (
                0.434,
                "The user believes functional programming leads to better code quality",
            ),
        ],
        "temporal": [
            (
                0.455,
                "Time: 12:00 on Wednesday (afternoon) | Subject: ai_future | Observation: The user thinks AI will replace most software developers within 10 years",
            ),
            (
                0.358,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.326,
                "Time: 12:00 on Wednesday (afternoon) | Subject: typescript_opinion | Observation: The user now says they love TypeScript but previously called it verbose",
            ),
            (
                0.326,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes",
            ),
        ],
    },
    "What are the user's views on artificial intelligence?": {
        "semantic": [
            (
                0.588,
                "Subject: ai_future | Type: belief | Observation: The user thinks AI will replace most software developers within 10 years | Quote: AI will make most programmers obsolete by 2035",
            ),
            (
                0.566,
                "The user thinks AI will replace most software developers within 10 years",
            ),
            (0.514, "AI will make most programmers obsolete by 2035"),
            (0.493, "I find backend logic more interesting than UI work"),
        ],
        "temporal": [
            (
                0.521,
                "Time: 12:00 on Wednesday (afternoon) | Subject: ai_future | Observation: The user thinks AI will replace most software developers within 10 years",
            ),
            (
                0.42,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.401,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky",
            ),
            (
                0.4,
                "Time: 12:00 on Wednesday (afternoon) | Subject: humans | Observation: The user thinks that all men must die.",
            ),
        ],
    },
    "Has the user changed their mind about TypeScript?": {
        "semantic": [
            (
                0.617,
                "The user now says they love TypeScript but previously called it verbose",
            ),
            (
                0.576,
                "Subject: typescript_opinion | Type: contradiction | Observation: The user now says they love TypeScript but previously called it verbose | Quote: TypeScript has too much boilerplate vs TypeScript makes my code so much cleaner",
            ),
            (
                0.491,
                "TypeScript has too much boilerplate vs TypeScript makes my code so much cleaner",
            ),
            (0.416, "The user always refactors to pure functions"),
        ],
        "temporal": [
            (
                0.566,
                "Time: 12:00 on Wednesday (afternoon) | Subject: typescript_opinion | Observation: The user now says they love TypeScript but previously called it verbose",
            ),
            (
                0.39,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces",
            ),
            (
                0.383,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript",
            ),
            (
                0.376,
                "Time: 12:00 on Wednesday (afternoon) | Subject: editor_preference | Observation: The user prefers Vim over VS Code for editing",
            ),
        ],
    },
    "Are there any contradictions in the user's preferences?": {
        "semantic": [
            (0.536, "The user claims to prefer tabs but their code uses spaces"),
            (
                0.535,
                "Subject: indentation_preference | Type: contradiction | Observation: The user claims to prefer tabs but their code uses spaces | Quote: Tabs are better than spaces vs code consistently uses 2-space indentation",
            ),
            (
                0.533,
                "Subject: pure_functions | Type: contradiction | Observation: The user said pure functions are yucky | Quote: Pure functions are yucky",
            ),
            (
                0.507,
                "Subject: typescript_opinion | Type: contradiction | Observation: The user now says they love TypeScript but previously called it verbose | Quote: TypeScript has too much boilerplate vs TypeScript makes my code so much cleaner",
            ),
        ],
        "temporal": [
            (
                0.467,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
            (
                0.466,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces",
            ),
            (
                0.457,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky",
            ),
            (
                0.455,
                "Time: 12:00 on Wednesday (afternoon) | Subject: database_preference | Observation: The user prefers PostgreSQL over MongoDB for most applications",
            ),
        ],
    },
    "What does the user think about software testing?": {
        "semantic": [
            (
                0.638,
                "Subject: testing_philosophy | Type: belief | Observation: The user believes unit tests are a waste of time for prototypes | Quote: Writing tests for throwaway code slows development",
            ),
            (0.622, "The user believes unit tests are a waste of time for prototypes"),
            (
                0.615,
                "Subject: code_quality | Type: belief | Observation: The user believes code reviews are essential for quality | Quote: Code reviews catch bugs that automated testing misses",
            ),
            (0.603, "The user believes code reviews are essential for quality"),
        ],
        "temporal": [
            (
                0.568,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes",
            ),
            (
                0.49,
                "Time: 12:00 on Wednesday (afternoon) | Subject: code_quality | Observation: The user believes code reviews are essential for quality",
            ),
            (
                0.474,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.452,
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
                0.546,
                "The user always writes documentation before implementing features",
            ),
            (0.521, "I document the API design before writing any code"),
            (
                0.495,
                "Subject: debugging_approach | Type: behavior | Observation: The user debugs by adding print statements rather than using a debugger | Quote: When debugging, I just add console.log everywhere",
            ),
        ],
        "temporal": [
            (
                0.5,
                "Time: 12:00 on Wednesday (afternoon) | Subject: documentation_habits | Observation: The user always writes documentation before implementing features",
            ),
            (
                0.437,
                "Time: 12:00 on Wednesday (afternoon) | Subject: version_control_style | Observation: The user prefers small, focused commits over large feature branches",
            ),
            (
                0.435,
                "Time: 12:00 on Wednesday (afternoon) | Subject: indentation_preference | Observation: The user claims to prefer tabs but their code uses spaces",
            ),
            (
                0.435,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
        ],
    },
    "What are the user's collaboration preferences?": {
        "semantic": [
            (
                0.652,
                "Subject: collaboration_preference | Type: preference | Observation: The user prefers pair programming for complex problems | Quote: Two heads are better than one when solving hard problems",
            ),
            (0.585, "The user prefers pair programming for complex problems"),
            (
                0.536,
                "Subject: version_control_style | Type: preference | Observation: The user prefers small, focused commits over large feature branches | Quote: I like to commit small, logical changes frequently",
            ),
            (
                0.522,
                "Subject: domain_preference | Type: preference | Observation: The user prefers working on backend systems over frontend UI | Quote: I find backend logic more interesting than UI work",
            ),
        ],
        "temporal": [
            (
                0.589,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems",
            ),
            (
                0.502,
                "Time: 12:00 on Wednesday (afternoon) | Subject: version_control_style | Observation: The user prefers small, focused commits over large feature branches",
            ),
            (
                0.475,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
            (
                0.464,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work",
            ),
        ],
    },
    "What does the user think about remote work?": {
        "semantic": [
            (0.705, "The user thinks remote work is more productive than office work"),
            (
                0.658,
                "Subject: work_environment | Type: belief | Observation: The user thinks remote work is more productive than office work | Quote: I get more done working from home",
            ),
            (0.603, "I get more done working from home"),
            (0.499, "The user prefers working on backend systems over frontend UI"),
        ],
        "temporal": [
            (
                0.583,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work",
            ),
            (
                0.413,
                "Time: 12:00 on Wednesday (afternoon) | Subject: testing_philosophy | Observation: The user believes unit tests are a waste of time for prototypes",
            ),
            (
                0.412,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems",
            ),
            (
                0.409,
                "Time: 12:00 on Wednesday (afternoon) | Subject: version_control_style | Observation: The user prefers small, focused commits over large feature branches",
            ),
        ],
    },
    "What are the user's productivity methods?": {
        "semantic": [
            (
                0.573,
                "Subject: productivity_methods | Type: behavior | Observation: The user takes breaks every 25 minutes using the Pomodoro technique | Quote: I use 25-minute work intervals with 5-minute breaks",
            ),
            (
                0.526,
                "The user takes breaks every 25 minutes using the Pomodoro technique",
            ),
            (0.52, "I use 25-minute work intervals with 5-minute breaks"),
            (0.512, "The user thinks remote work is more productive than office work"),
        ],
        "temporal": [
            (
                0.531,
                "Time: 12:00 on Wednesday (afternoon) | Subject: productivity_methods | Observation: The user takes breaks every 25 minutes using the Pomodoro technique",
            ),
            (
                0.48,
                "Time: 12:00 on Wednesday (afternoon) | Subject: work_environment | Observation: The user thinks remote work is more productive than office work",
            ),
            (
                0.434,
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
            (0.577, "The user is currently learning Rust in their spare time"),
            (
                0.55,
                "Subject: learning_activities | Type: general | Observation: The user is currently learning Rust in their spare time | Quote: I'm picking up Rust on weekends",
            ),
            (0.542, "I'm picking up Rust on weekends"),
            (0.516, "The user primarily works with Python and JavaScript"),
        ],
        "temporal": [
            (
                0.522,
                "Time: 12:00 on Wednesday (afternoon) | Subject: learning_activities | Observation: The user is currently learning Rust in their spare time",
            ),
            (
                0.492,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript",
            ),
            (
                0.487,
                "Time: 12:00 on Wednesday (afternoon) | Subject: experience_level | Observation: The user has 8 years of professional programming experience",
            ),
            (
                0.455,
                "Time: 12:00 on Wednesday (afternoon) | Subject: education_background | Observation: The user graduated with a Computer Science degree from Stanford",
            ),
        ],
    },
    "What does the user think about cooking?": {
        "semantic": [
            (0.489, "I find backend logic more interesting than UI work"),
            (0.462, "The user prefers working on backend systems over frontend UI"),
            (0.455, "The user said pure functions are yucky"),
            (
                0.455,
                "The user believes functional programming leads to better code quality",
            ),
        ],
        "temporal": [
            (
                0.379,
                "Time: 12:00 on Wednesday (afternoon) | Subject: pure_functions | Observation: The user said pure functions are yucky",
            ),
            (
                0.376,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_philosophy | Observation: The user believes functional programming leads to better code quality",
            ),
            (
                0.375,
                "Time: 12:00 on Wednesday (afternoon) | Subject: typescript_opinion | Observation: The user now says they love TypeScript but previously called it verbose",
            ),
            (
                0.359,
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
            (0.514, "The user prefers functional programming over OOP"),
            (0.507, "The user prefers working on backend systems over frontend UI"),
            (0.505, "The user prefers working late at night"),
        ],
        "temporal": [
            (
                0.477,
                "Time: 12:00 on Wednesday (afternoon) | Subject: domain_preference | Observation: The user prefers working on backend systems over frontend UI",
            ),
            (
                0.475,
                "Time: 12:00 on Wednesday (afternoon) | Subject: database_preference | Observation: The user prefers PostgreSQL over MongoDB for most applications",
            ),
            (
                0.459,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP",
            ),
            (
                0.455,
                "Time: 12:00 on Wednesday (afternoon) | Subject: collaboration_preference | Observation: The user prefers pair programming for complex problems",
            ),
        ],
    },
    "What music does the user like?": {
        "semantic": [
            (
                0.493,
                "Subject: domain_preference | Type: preference | Observation: The user prefers working on backend systems over frontend UI | Quote: I find backend logic more interesting than UI work",
            ),
            (0.491, "The user prefers working late at night"),
            (0.49, "The user prefers functional programming over OOP"),
            (0.489, "The user primarily works with Python and JavaScript"),
        ],
        "temporal": [
            (
                0.468,
                "Time: 12:00 on Wednesday (afternoon) | Subject: typescript_opinion | Observation: The user now says they love TypeScript but previously called it verbose",
            ),
            (
                0.456,
                "Time: 12:00 on Wednesday (afternoon) | Subject: primary_languages | Observation: The user primarily works with Python and JavaScript",
            ),
            (
                0.447,
                "Time: 12:00 on Wednesday (afternoon) | Subject: programming_paradigms | Observation: The user prefers functional programming over OOP",
            ),
            (
                0.443,
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
            (pytest.approx(i.score, 0.1), chunk_map[str(i.id)].content)  # type: ignore
            for i in sorted(results, key=lambda x: x.score, reverse=True)
        ][:4]

    results = {}
    for query, expected in EXPECTED_OBSERVATION_RESULTS.items():
        search_vector = embed_text(
            [extract.DataChunk(data=[query])], input_type="query"
        )[0]
        semantic_results = get_top(search_vector, "semantic")
        temporal_results = get_top(search_vector, "temporal")
        results[query] = {
            "semantic": semantic_results,
            "temporal": temporal_results,
        }
        assert semantic_results == expected["semantic"]
        assert temporal_results == expected["temporal"]

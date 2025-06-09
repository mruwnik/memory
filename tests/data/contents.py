import hashlib
import pathlib
from bs4 import BeautifulSoup
from markdownify import markdownify
from PIL import Image

DATA_DIR = pathlib.Path(__file__).parent

SAMPLE_HTML = f"""
<html>
<body>
    <h1>The Evolution of Programming Languages</h1>
    
    <p>Programming languages have undergone tremendous evolution since the early days of computing. 
    From the machine code and assembly languages of the 1940s to the high-level, expressive languages 
    we use today, each generation has built upon the lessons learned from its predecessors. Languages 
    like FORTRAN and COBOL pioneered the concept of human-readable code, while later innovations like 
    object-oriented programming in languages such as Smalltalk and C++ revolutionized how we structure 
    and organize our programs.</p>
    
    <img src="{DATA_DIR / "lang_timeline.png"}" alt="Timeline of programming language evolution" width="600" height="400">
    
    <p>The rise of functional programming paradigms has brought mathematical rigor and immutability 
    to the forefront of software development. Languages like Haskell, Lisp, and more recently Rust 
    and Elm have demonstrated the power of pure functions and type systems in creating more reliable 
    and maintainable code. These paradigms emphasize the elimination of side effects and the treatment 
    of computation as the evaluation of mathematical functions.</p>
    <p>Modern development has also seen the emergence of domain-specific languages and the resurgence 
    of interest in memory safety. The advent of languages like Python and JavaScript has democratized 
    programming by lowering the barrier to entry, while systems languages like Rust have proven that 
    performance and safety need not be mutually exclusive. The ongoing development of WebAssembly 
    promises to bring high-performance computing to web browsers in ways previously unimaginable.</p>
    
    <img src="{DATA_DIR / "code_complexity.jpg"}" alt="Visual representation of code complexity over time" width="500" height="300">
    
    <p>Looking toward the future, we see emerging trends in quantum programming languages, AI-assisted 
    code generation, and the continued evolution toward more expressive type systems. The challenge 
    for tomorrow's language designers will be balancing expressiveness with simplicity, performance 
    with safety, and innovation with backward compatibility. As computing continues to permeate every 
    aspect of human life, the languages we use to command these machines will undoubtedly continue 
    to evolve and shape the digital landscape.</p>
    
    <p>The emergence of cloud computing and distributed systems has also driven new paradigms in 
    language design. Languages like Go and Elixir have been specifically crafted to excel in 
    concurrent and distributed environments, while the rise of microservices has renewed interest 
    in polyglot programming approaches. These developments reflect a broader shift toward languages 
    that are not just powerful tools for individual developers, but robust foundations for building 
    scalable, resilient systems that can handle the demands of modern internet-scale applications.</p>
    
    <p>Perhaps most intriguingly, the intersection of programming languages with artificial intelligence 
    is opening entirely new frontiers. Differentiable programming languages are enabling new forms of 
    machine learning research, while large language models are beginning to reshape how we think about 
    code generation and developer tooling. As we stand on the brink of an era where AI systems may 
    become active participants in the programming process itself, the very nature of what constitutes 
    a programming language—and who or what programs in it—may be fundamentally transformed.</p>
</body>
</html>
"""
SECOND_PAGE = """
<div>
    <h2>The Impact of Open Source on Language Development</h2>
    
    <p>The open source movement has fundamentally transformed how programming languages are developed, 
    distributed, and evolved. Unlike the proprietary languages of earlier decades, modern language 
    development often occurs in public repositories where thousands of contributors can participate 
    in the design process. Languages like Python, JavaScript, and Rust have benefited enormously 
    from this collaborative approach, with their ecosystems growing rapidly through community-driven 
    package managers and extensive third-party libraries.</p>
    
    <p>This democratization of language development has led to faster innovation cycles and more 
    responsive adaptation to developer needs. When a language feature proves problematic or a new 
    paradigm emerges, open source languages can quickly incorporate changes through their community 
    governance processes. The result has been an unprecedented period of language experimentation 
    and refinement, where ideas can be tested, refined, and adopted across multiple language 
    communities simultaneously.</p>
    
    <p>Furthermore, the open source model has enabled the rise of domain-specific languages that 
    might never have been commercially viable under traditional development models. From specialized 
    query languages for databases to configuration management tools, the low barrier to entry for 
    language creation has fostered an explosion of linguistic diversity in computing, each tool 
    optimized for specific problem domains and user communities.</p>
    
    <p>The collaborative nature of open source development has also revolutionized language tooling 
    and developer experience. Modern languages benefit from rich ecosystems of editors, debuggers, 
    profilers, and static analysis tools, all developed by passionate communities who understand 
    the daily challenges faced by practitioners. This has created a virtuous cycle where better 
    tooling attracts more developers, who in turn contribute improvements that make the language 
    even more accessible and powerful.</p>
    
    <p>Version control systems like Git have enabled unprecedented transparency in language evolution, 
    allowing developers to trace the reasoning behind every design decision through detailed commit 
    histories and issue discussions. This historical record serves not only as documentation but as 
    a learning resource for future language designers, helping them understand the trade-offs and 
    considerations that shaped successful language features.</p>
    
    <p>The economic implications of open source language development cannot be overstated. By removing 
    licensing barriers and vendor lock-in, open source languages have democratized access to powerful 
    programming tools across the globe. This has enabled innovation in regions and sectors that might 
    otherwise have been excluded from the software revolution, fostering a truly global community of 
    software creators and problem solvers.</p>
</div>
"""

CHUNKS: list[str] = [
    """The Evolution of Programming Languages
====================================== 
Programming languages have undergone tremendous evolution since the early days of computing. 
 From the machine code and assembly languages of the 1940s to the high\\-level, expressive languages 
 we use today, each generation has built upon the lessons learned from its predecessors. Languages 
 like FORTRAN and COBOL pioneered the concept of human\\-readable code, while later innovations like 
 object\\-oriented programming in languages such as Smalltalk and C\\+\\+ revolutionized how we structure 
 and organize our programs. 
![Timeline of programming language evolution](/Users/dan/code/memory/tests/data/lang_timeline.png)
The rise of functional programming paradigms has brought mathematical rigor and immutability 
 to the forefront of software development. Languages like Haskell, Lisp, and more recently Rust 
 and Elm have demonstrated the power of pure functions and type systems in creating more reliable 
 and maintainable code. These paradigms emphasize the elimination of side effects and the treatment 
 of computation as the evaluation of mathematical functions. 
Modern development has also seen the emergence of domain\\-specific languages and the resurgence 
 of interest in memory safety. The advent of languages like Python and JavaScript has democratized 
 programming by lowering the barrier to entry, while systems languages like Rust have proven that 
 performance and safety need not be mutually exclusive. The ongoing development of WebAssembly 
 promises to bring high\\-performance computing to web browsers in ways previously unimaginable. 
![Visual representation of code complexity over time](/Users/dan/code/memory/tests/data/code_complexity.jpg)
Looking toward the future, we see emerging trends in quantum programming languages, AI\\-assisted 
 code generation, and the continued evolution toward more expressive type systems. The challenge 
 for tomorrow's language designers will be balancing expressiveness with simplicity, performance 
 with safety, and innovation with backward compatibility. As computing continues to permeate every 
 aspect of human life, the languages we use to command these machines will undoubtedly continue 
 to evolve and shape the digital landscape.""",
    """
As computing continues to permeate every 
 aspect of human life, the languages we use to command these machines will undoubtedly continue 
 to evolve and shape the digital landscape. 
The emergence of cloud computing and distributed systems has also driven new paradigms in 
 language design. Languages like Go and Elixir have been specifically crafted to excel in 
 concurrent and distributed environments, while the rise of microservices has renewed interest 
 in polyglot programming approaches. These developments reflect a broader shift toward languages 
 that are not just powerful tools for individual developers, but robust foundations for building 
 scalable, resilient systems that can handle the demands of modern internet\\-scale applications. 
Perhaps most intriguingly, the intersection of programming languages with artificial intelligence 
 is opening entirely new frontiers. Differentiable programming languages are enabling new forms of 
 machine learning research, while large language models are beginning to reshape how we think about 
 code generation and developer tooling. As we stand on the brink of an era where AI systems may 
 become active participants in the programming process itself, the very nature of what constitutes 
 a programming language—and who or what programs in it—may be fundamentally transformed.""",
]
TWO_PAGE_CHUNKS: list[str] = [
    """
The Evolution of Programming Languages
====================================== 
Programming languages have undergone tremendous evolution since the early days of computing. 
 From the machine code and assembly languages of the 1940s to the high\-level, expressive languages 
 we use today, each generation has built upon the lessons learned from its predecessors. Languages 
 like FORTRAN and COBOL pioneered the concept of human\-readable code, while later innovations like 
 object\-oriented programming in languages such as Smalltalk and C\+\+ revolutionized how we structure 
 and organize our programs. 
![Timeline of programming language evolution](/Users/dan/code/memory/tests/data/lang_timeline.png)
The rise of functional programming paradigms has brought mathematical rigor and immutability 
 to the forefront of software development. Languages like Haskell, Lisp, and more recently Rust 
 and Elm have demonstrated the power of pure functions and type systems in creating more reliable 
 and maintainable code. These paradigms emphasize the elimination of side effects and the treatment 
 of computation as the evaluation of mathematical functions. 
Modern development has also seen the emergence of domain\-specific languages and the resurgence 
 of interest in memory safety. The advent of languages like Python and JavaScript has democratized 
 programming by lowering the barrier to entry, while systems languages like Rust have proven that 
 performance and safety need not be mutually exclusive. The ongoing development of WebAssembly 
 promises to bring high\-performance computing to web browsers in ways previously unimaginable. 
![Visual representation of code complexity over time](/Users/dan/code/memory/tests/data/code_complexity.jpg)
Looking toward the future, we see emerging trends in quantum programming languages, AI\-assisted 
 code generation, and the continued evolution toward more expressive type systems. The challenge 
 for tomorrow's language designers will be balancing expressiveness with simplicity, performance 
 with safety, and innovation with backward compatibility. As computing continues to permeate every 
 aspect of human life, the languages we use to command these machines will undoubtedly continue 
 to evolve and shape the digital landscape.
""",
    """
As computing continues to permeate every 
 aspect of human life, the languages we use to command these machines will undoubtedly continue 
 to evolve and shape the digital landscape. 
The emergence of cloud computing and distributed systems has also driven new paradigms in 
 language design. Languages like Go and Elixir have been specifically crafted to excel in 
 concurrent and distributed environments, while the rise of microservices has renewed interest 
 in polyglot programming approaches. These developments reflect a broader shift toward languages 
 that are not just powerful tools for individual developers, but robust foundations for building 
 scalable, resilient systems that can handle the demands of modern internet\-scale applications. 
Perhaps most intriguingly, the intersection of programming languages with artificial intelligence 
 is opening entirely new frontiers. Differentiable programming languages are enabling new forms of 
 machine learning research, while large language models are beginning to reshape how we think about 
 code generation and developer tooling. As we stand on the brink of an era where AI systems may 
 become active participants in the programming process itself, the very nature of what constitutes 
 a programming language—and who or what programs in it—may be fundamentally transformed. 
The Impact of Open Source on Language Development
------------------------------------------------- 
The open source movement has fundamentally transformed how programming languages are developed, 
 distributed, and evolved. Unlike the proprietary languages of earlier decades, modern language 
 development often occurs in public repositories where thousands of contributors can participate 
 in the design process. Languages like Python, JavaScript, and Rust have benefited enormously 
 from this collaborative approach, with their ecosystems growing rapidly through community\-driven 
 package managers and extensive third\-party libraries. 
This democratization of language development has led to faster innovation cycles and more 
 responsive adaptation to developer needs. When a language feature proves problematic or a new 
 paradigm emerges, open source languages can quickly incorporate changes through their community 
 governance processes. The result has been an unprecedented period of language experimentation 
 and refinement, where ideas can be tested, refined, and adopted across multiple language 
 communities simultaneously.""",
    """
The result has been an unprecedented period of language experimentation 
 and refinement, where ideas can be tested, refined, and adopted across multiple language 
 communities simultaneously. 
Furthermore, the open source model has enabled the rise of domain\-specific languages that 
 might never have been commercially viable under traditional development models. From specialized 
 query languages for databases to configuration management tools, the low barrier to entry for 
 language creation has fostered an explosion of linguistic diversity in computing, each tool 
 optimized for specific problem domains and user communities. 
The collaborative nature of open source development has also revolutionized language tooling 
 and developer experience. Modern languages benefit from rich ecosystems of editors, debuggers, 
 profilers, and static analysis tools, all developed by passionate communities who understand 
 the daily challenges faced by practitioners. This has created a virtuous cycle where better 
 tooling attracts more developers, who in turn contribute improvements that make the language 
 even more accessible and powerful. 
Version control systems like Git have enabled unprecedented transparency in language evolution, 
 allowing developers to trace the reasoning behind every design decision through detailed commit 
 histories and issue discussions. This historical record serves not only as documentation but as 
 a learning resource for future language designers, helping them understand the trade\-offs and 
 considerations that shaped successful language features. 
The economic implications of open source language development cannot be overstated. By removing 
 licensing barriers and vendor lock\-in, open source languages have democratized access to powerful 
 programming tools across the globe. This has enabled innovation in regions and sectors that might 
 otherwise have been excluded from the software revolution, fostering a truly global community of 
 software creators and problem solvers.
""",
]

SAMPLE_MARKDOWN = markdownify(SAMPLE_HTML)
SAMPLE_TEXT = BeautifulSoup(SAMPLE_HTML, "html.parser").get_text()
SECOND_PAGE_MARKDOWN = markdownify(SECOND_PAGE)
SECOND_PAGE_TEXT = BeautifulSoup(SECOND_PAGE, "html.parser").get_text()

SAMPLE_EMAIL = f"""From: john.doe@techcorp.com
To: research-team@techcorp.com, jane.smith@university.edu
CC: newsletter@programming-weekly.com
Subject: The Evolution of Programming Languages - Research Article
Date: Wed, 15 Jan 2025 14:30:00 +0000
Message-ID: <20250115143000.12345@techcorp.com>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="----=_NextPart_000_0001_01DA1234.56789ABC"

This is a multi-part message in MIME format.

------=_NextPart_000_0001_01DA1234.56789ABC
Content-Type: text/html; charset=utf-8
Content-Transfer-Encoding: quoted-printable

{SAMPLE_HTML}

------=_NextPart_000_0001_01DA1234.56789ABC
Content-Type: image/png
Content-Disposition: attachment; filename="lang_timeline.png"
Content-Transfer-Encoding: base64

iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==

------=_NextPart_000_0001_01DA1234.56789ABC
Content-Type: image/jpeg
Content-Disposition: attachment; filename="code_complexity.jpg"
Content-Transfer-Encoding: base64

/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB

------=_NextPart_000_0001_01DA1234.56789ABC--
"""


def image_hash(image: Image.Image) -> str:
    return hashlib.sha256(image.tobytes()).hexdigest()


LANG_TIMELINE = Image.open(DATA_DIR / "lang_timeline.png")
CODE_COMPLEXITY = Image.open(DATA_DIR / "code_complexity.jpg")
LANG_TIMELINE_HASH = image_hash(LANG_TIMELINE)
CODE_COMPLEXITY_HASH = image_hash(CODE_COMPLEXITY)

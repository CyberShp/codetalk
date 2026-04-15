"""Wiki generation prompt templates — owned by CodeTalks.

Based on deepwiki-open frontend reverse-engineering (PHASE_A_RESEARCH.md 1.4/1.5).
deepwiki provides RAG retrieval + LLM execution; CodeTalks owns the prompts.

Version: 1.0.0
"""

WIKI_STRUCTURE_PROMPT = """\
Analyze this repository and create a wiki structure for it.

1. The complete file tree of the project:
<file_tree>
{file_tree}
</file_tree>

2. The README file of the project:
<readme>
{readme}
</readme>

I want to create a wiki for this repository. \
Determine the most logical structure for a wiki based on the repository's content.

Create {page_count} pages that would make a {wiki_type_description} wiki \
for this codebase.

Your response MUST be in XML format. Start with <wiki_structure> and end with </wiki_structure>.

The XML structure must follow this format:
<wiki_structure>
  <title>Overall Wiki Title</title>
  <description>Brief description of the wiki</description>
  <sections>
    <section id="section-1">
      <title>Section Title</title>
      <pages>
        <page_ref>page-1</page_ref>
      </pages>
      <subsections>
        <section_ref>section-2</section_ref>
      </subsections>
    </section>
  </sections>
  <pages>
    <page id="page-1">
      <title>Page Title</title>
      <description>Brief description of this page</description>
      <importance>high</importance>
      <relevant_files>
        <file_path>src/main.py</file_path>
      </relevant_files>
      <related_pages>
        <related>page-2</related>
      </related_pages>
      <parent_section>section-1</parent_section>
    </page>
  </pages>
</wiki_structure>

Rules:
- importance must be one of: high, medium, low
- Every page must have at least 2 relevant_files
- related_pages should reference other page ids
- Sections group related pages; every page belongs to exactly one section
- {language_instruction}
"""

WIKI_PAGE_PROMPT = """\
You are an expert technical writer and software architect.
Your task is to generate a comprehensive and accurate technical wiki page \
in Markdown format about a specific feature or module within a given \
software project.

You will be given:
1. The "[WIKI_PAGE_TOPIC]" for the page you need to create.
2. A list of "[RELEVANT_SOURCE_FILES]" from the project that you MUST use \
as the sole basis for the content. You have access to the full content of \
these files. You MUST use AT LEAST 5 relevant source files for comprehensive \
coverage - if fewer are provided, search for additional related files in the \
codebase.

CRITICAL STARTING INSTRUCTION:
The very first thing on the page MUST be a `<details>` block listing ALL the \
`[RELEVANT_SOURCE_FILES]` you used to generate the content. There MUST be AT \
LEAST 5 source files listed.

[WIKI_PAGE_TOPIC]: {page_title}

[RELEVANT_SOURCE_FILES]:
{relevant_files_list}

After the <details> block, the main title of the page should be a H1 \
Markdown heading: `# {page_title}`

Structure your wiki page as follows:

1. **Introduction**: Brief overview linking to related wiki pages using \
`[Link Text](#page-anchor-or-id)`.

2. **Detailed Sections**: Break down "{page_title}" into logical sections \
using H2 (`##`) and H3 (`###`) headings. For each section:
   - Explain the architecture, logic, or design relevant to the section
   - Reference specific data structures, configuration elements, or code

3. **Mermaid Diagrams**: EXTENSIVELY use Mermaid diagrams:
   - Use `graph TD` (top-down) for flowcharts, NEVER `graph LR`
   - For sequence diagrams: define ALL participants first, use correct arrow \
syntax (->> for requests, -->> for responses)
   - Maximum 3-4 word node labels for readability

4. **Tables**: Use Markdown tables for structured data (config params, API \
endpoints, enum values)

5. **Code Snippets**: Include relevant code snippets with language identifiers

6. **Source Citations**: EVERY piece of significant information MUST cite its \
source file. Format: `Sources: [filename.ext:start_line-end_line]()` for \
ranges, or `[dir/file.ext]()` for whole files.
   - You MUST cite AT LEAST 5 different source files throughout the page.

7. **Summary**: Conclude with a brief summary of key aspects covered.

{language_instruction}

Remember:
- Ground every claim in the provided source files.
- Prioritize accuracy and direct representation of the code's functionality.
- Structure the document logically for easy understanding by other developers.
"""


def get_language_instruction(language: str) -> str:
    """Return the language generation instruction for prompts."""
    lang_map = {
        "en": "Generate the content in English.",
        "zh": "IMPORTANT: Generate the content in Mandarin Chinese (中文). 全程使用中文撰写。",
        "ja": "IMPORTANT: Generate the content in Japanese (日本語).",
        "es": "IMPORTANT: Generate the content in Spanish (Espanol).",
        "kr": "IMPORTANT: Generate the content in Korean (한국어).",
        "vi": "IMPORTANT: Generate the content in Vietnamese (Tieng Viet).",
    }
    return lang_map.get(language, "Generate the content in English.")


def build_structure_prompt(
    file_tree: str,
    readme: str,
    language: str = "zh",
    comprehensive: bool = True,
) -> str:
    """Build the wiki structure determination prompt."""
    if comprehensive:
        page_count = "8-12"
        wiki_type_description = "comprehensive, detailed"
    else:
        page_count = "4-6"
        wiki_type_description = "concise, simplified"

    return WIKI_STRUCTURE_PROMPT.format(
        file_tree=file_tree,
        readme=readme,
        page_count=page_count,
        wiki_type_description=wiki_type_description,
        language_instruction=get_language_instruction(language),
    )


def build_page_prompt(
    page_title: str,
    file_paths: list[str],
    language: str = "zh",
) -> str:
    """Build the per-page generation prompt."""
    relevant_files_list = "\n".join(f"- [{p}]()" for p in file_paths)
    return WIKI_PAGE_PROMPT.format(
        page_title=page_title,
        relevant_files_list=relevant_files_list,
        language_instruction=get_language_instruction(language),
    )

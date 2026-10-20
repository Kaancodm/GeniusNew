"""Ten fixed, harmless operator tasks per model; CI uses a fake Ollama server.

The same ten tasks flow through both model roles in sequence, never in
parallel. Passing transport checks does not establish model answer quality.
"""

CASES = (
    ("summary", "Summarize: The test team wrote two cases and ran both."),
    ("code_draft", "Draft a pure Python function to add two integers."),
    ("summary", "Summarize: Alice sent a report. Bob reviewed it."),
    ("code_draft", "Draft a pure Python function that returns the maximum of a list."),
    ("summary", "Summarize: A library closes at six and opens at nine."),
    ("code_draft", "Draft a pure Python function that reverses a string."),
    ("summary", "Summarize: Three plants need water every Thursday."),
    ("code_draft", "Draft a pure Python function that counts vowel letters."),
    ("summary", "Summarize: The blue notebook is on the wooden desk."),
    ("code_draft", "Draft a pure Python function that checks if a number is even."),
)

"""Generate PDF from Markdown documentation."""

import markdown
from weasyprint import HTML, CSS

# Read markdown
with open("AllocateAI_Technical_Documentation.md", "r", encoding="utf-8") as f:
    md_content = f.read()

# Convert to HTML
html_content = markdown.markdown(
    md_content,
    extensions=["tables", "fenced_code", "toc"]
)

# Wrap in full HTML with styling
full_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Allocate.AI Technical Documentation</title>
</head>
<body>
{html_content}
</body>
</html>
"""

# CSS for professional look
css = CSS(string="""
@page {{
    size: A4;
    margin: 2cm;
    @top-center {{
        content: "Allocate.AI Technical Documentation";
        font-size: 10px;
        color: #666;
    }}
    @bottom-center {{
        content: "Page " counter(page) " of " counter(pages);
        font-size: 10px;
        color: #666;
    }}
}}

body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 11px;
    line-height: 1.5;
    color: #333;
}}

h1 {{
    color: #1a1a2e;
    font-size: 24px;
    border-bottom: 3px solid #4a69bd;
    padding-bottom: 10px;
    margin-top: 30px;
    page-break-after: avoid;
}}

h2 {{
    color: #1a1a2e;
    font-size: 18px;
    border-bottom: 1px solid #ddd;
    padding-bottom: 5px;
    margin-top: 25px;
    page-break-after: avoid;
}}

h3 {{
    color: #333;
    font-size: 14px;
    margin-top: 20px;
    page-break-after: avoid;
}}

h4 {{
    color: #555;
    font-size: 12px;
    margin-top: 15px;
}}

code {{
    background-color: #f4f4f4;
    padding: 2px 6px;
    border-radius: 3px;
    font-family: "Fira Code", "Consolas", monospace;
    font-size: 10px;
}}

pre {{
    background-color: #282c34;
    color: #abb2bf;
    padding: 15px;
    border-radius: 5px;
    overflow-x: auto;
    font-size: 9px;
    line-height: 1.4;
    page-break-inside: avoid;
}}

pre code {{
    background-color: transparent;
    padding: 0;
    color: inherit;
}}

table {{
    width: 100%;
    border-collapse: collapse;
    margin: 15px 0;
    font-size: 10px;
    page-break-inside: avoid;
}}

th {{
    background-color: #4a69bd;
    color: white;
    padding: 10px;
    text-align: left;
    font-weight: 600;
}}

td {{
    padding: 8px 10px;
    border: 1px solid #ddd;
}}

tr:nth-child(even) {{
    background-color: #f9f9f9;
}}

blockquote {{
    border-left: 4px solid #4a69bd;
    padding-left: 15px;
    margin: 15px 0;
    color: #666;
    font-style: italic;
}}

ul, ol {{
    margin: 10px 0;
    padding-left: 25px;
}}

li {{
    margin: 5px 0;
}}

hr {{
    border: none;
    border-top: 2px solid #eee;
    margin: 30px 0;
}}

a {{
    color: #4a69bd;
    text-decoration: none;
}}

strong {{
    color: #1a1a2e;
}}

/* Title page styling */
h1:first-of-type {{
    font-size: 32px;
    text-align: center;
    border-bottom: none;
    margin-top: 100px;
    margin-bottom: 50px;
}}
""")

# Generate PDF
html = HTML(string=full_html)
html.write_pdf("AllocateAI_Technical_Documentation.pdf", stylesheets=[css])

print("PDF generated: AllocateAI_Technical_Documentation.pdf")

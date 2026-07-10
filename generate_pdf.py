import os
import subprocess
import sys

# Ensure fpdf2 is installed
try:
    from fpdf import FPDF
except ImportError:
    print("Installing fpdf2 library...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "fpdf2"])
    from fpdf import FPDF

class PDFReport(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(156, 163, 175) # var(--text-secondary) equivalent
        self.cell(0, 10, "vZoom Meeting Platform - Project Report", border=0, align="R")
        self.ln(12)
        
    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(156, 163, 175)
        self.cell(0, 10, f"Page {self.page_no()}", border=0, align="C")

def generate_pdf(md_path, pdf_path):
    pdf = PDFReport()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # Set Margins
    pdf.set_left_margin(15)
    pdf.set_right_margin(15)
    
    # Content Title
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(30, 41, 59) # Slate 800
    pdf.cell(0, 15, "vZoom Meeting Platform", border=0, ln=True, align="L")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(100, 116, 139) # Slate 500
    pdf.cell(0, 6, "Platform Architecture, DB Models & Key Features", border=0, ln=True, align="L")
    
    pdf.ln(8)
    
    # Draw horizontal rule
    pdf.set_draw_color(226, 232, 240)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(6)
    
    # Read Markdown file
    with open(md_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    for line in lines:
        line_str = line.strip()
        if not line_str:
            pdf.ln(4)
            continue
            
        # Ignore Mermaid codeblocks and page metadata
        if line_str.startswith("```") or line_str.startswith("graph") or line_str.startswith("Client") or line_str.startswith("Daphne") or line_str.startswith("Channels") or line_str.startswith("Django") or line_str.startswith("DB"):
            continue
            
        # Parse headings
        if line_str.startswith("# "):
            # Main Title
            title_text = line_str[2:]
            pdf.set_font("Helvetica", "B", 20)
            pdf.set_text_color(30, 41, 59)
            pdf.ln(4)
            pdf.cell(0, 10, title_text, border=0, ln=True)
            pdf.ln(2)
        elif line_str.startswith("## "):
            heading_text = line_str[3:]
            pdf.set_font("Helvetica", "B", 14)
            pdf.set_text_color(37, 99, 235) # Accent Blue
            pdf.ln(6)
            pdf.cell(0, 8, heading_text, border=0, ln=True)
            pdf.ln(2)
        elif line_str.startswith("### "):
            heading_text = line_str[4:]
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(71, 85, 105) # Slate 600
            pdf.ln(4)
            pdf.cell(0, 6, heading_text, border=0, ln=True)
            pdf.ln(1)
        # Parse list items
        elif line_str.startswith("* ") or line_str.startswith("- "):
            text = line_str[2:]
            pdf.set_font("Helvetica", "", 10.5)
            pdf.set_text_color(51, 65, 85)
            pdf.cell(5, 5, chr(149), border=0, align="R")
            # Multiline write
            pdf.multi_cell(0, 5, text)
        elif line_str.startswith("1. ") or line_str.startswith("2. ") or line_str.startswith("3. ") or line_str.startswith("4. "):
            text = line_str[3:]
            num = line_str[:2]
            pdf.set_font("Helvetica", "", 10.5)
            pdf.set_text_color(51, 65, 85)
            pdf.cell(8, 5, num, border=0, align="R")
            pdf.multi_cell(0, 5, text)
        # Table rows or metadata
        elif line_str.startswith("|"):
            if "Filename" in line_str or "---" in line_str:
                continue
            parts = [p.strip() for p in line_str.split("|")[1:-1]]
            pdf.set_font("Helvetica", "B" if "Filename" in line_str else "", 9.5)
            pdf.set_text_color(51, 65, 85)
            # Custom simple table cells layout
            pdf.cell(35, 6, parts[0], border=1)
            pdf.cell(25, 6, parts[1], border=1)
            pdf.multi_cell(120, 6, parts[2], border=1)
        else:
            # Plain paragraph text
            pdf.set_font("Helvetica", "", 10.5)
            pdf.set_text_color(51, 65, 85)
            # Replace markdown bold syntax **
            processed_text = line_str.replace("**", "")
            pdf.multi_cell(0, 5.5, processed_text)
            
    pdf.output(pdf_path)
    print(f"Successfully generated PDF: {pdf_path}")

if __name__ == "__main__":
    current_dir = os.path.dirname(os.path.abspath(__file__))
    md_file = os.path.join(current_dir, "project_report.md")
    pdf_file = os.path.join(current_dir, "project_report.pdf")
    
    # If project_report.md doesn't exist, try to read from the artifact directory
    if not os.path.exists(md_file):
        md_file = r"C:\Users\Vijay\.gemini\antigravity\brain\fc12d6b9-ccd6-4e60-ba7a-ac618eef8846\project_report.md"
        
    generate_pdf(md_file, pdf_file)

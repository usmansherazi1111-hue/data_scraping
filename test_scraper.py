import os
import json

from flask import Flask, render_template_string, send_file, request
from dotenv import load_dotenv
from scrapegraphai.graphs import SmartScraperGraph
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment


# ============================================================
# LOAD ENVIRONMENT
# ============================================================

load_dotenv()


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)

EXCEL_FILE = "scraped_companies.xlsx"


# ============================================================
# SCRAPEGRAPHAI CONFIG
# ============================================================

graph_config = {
    "llm": {
        "api_key": os.getenv("OPENROUTER_API_KEY"),
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-oss-20b",
    },
    "verbose": True,
    "headless": False,
}


# ============================================================
# HTML
# ============================================================

HTML = """
<!DOCTYPE html>

<html>

<head>

    <title>Company Scraper</title>

    <style>

        * {
            box-sizing: border-box;
        }

        body {
            font-family: Arial, sans-serif;
            background: #f4f6f8;
            margin: 0;
            padding: 40px;
        }

        .container {
            max-width: 1100px;
            margin: auto;
            background: white;
            padding: 35px;
            border-radius: 12px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.08);
        }

        h1 {
            margin-top: 0;
            color: #111827;
        }

        h2 {
            margin-top: 35px;
            color: #111827;
        }

        p {
            color: #555;
        }

        .input-group {
            display: flex;
            gap: 10px;
            margin: 25px 0;
        }

        input {
            flex: 1;
            padding: 14px;
            border: 1px solid #ccc;
            border-radius: 7px;
            font-size: 16px;
        }

        button {
            padding: 14px 22px;
            border: none;
            border-radius: 7px;
            background: #2563eb;
            color: white;
            font-size: 15px;
            font-weight: bold;
            cursor: pointer;
        }

        button:hover {
            background: #1d4ed8;
        }

        .success {
            background: #ecfdf5;
            border: 1px solid #10b981;
            padding: 18px;
            border-radius: 7px;
            margin-top: 20px;
        }

        .error {
            background: #fef2f2;
            border: 1px solid #ef4444;
            padding: 18px;
            border-radius: 7px;
            margin-top: 20px;
            color: #991b1b;
        }

        .download {
            display: inline-block;
            padding: 14px 22px;
            margin-top: 15px;
            background: #16a34a;
            color: white;
            text-decoration: none;
            border-radius: 7px;
            font-weight: bold;
        }

        .download:hover {
            background: #15803d;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            table-layout: fixed;
        }

        th,
        td {
            border: 1px solid #d1d5db;
            padding: 12px;
            text-align: left;
            vertical-align: top;
            word-break: break-word;
        }

        th {
            background: #f1f5f9;
            font-weight: bold;
        }

        th:first-child,
        td:first-child {
            width: 30%;
        }

        th:last-child,
        td:last-child {
            width: 70%;
        }

        .empty {
            color: #999;
            font-style: italic;
        }

    </style>

</head>


<body>


<div class="container">


    <h1>Company Scraper</h1>


    <p>
        Enter a company website to extract publicly available
        company and contact information.
    </p>


    <form method="POST" action="/scrape">

        <div class="input-group">

            <input
                type="text"
                name="url"
                placeholder="https://example.com"
                value="{{ url }}"
                required
            >

            <button type="submit">
                Scrape Company
            </button>

        </div>

    </form>


    {% if error %}

        <div class="error">

            <strong>Error:</strong>
            {{ error }}

        </div>

    {% endif %}


    {% if data %}

        <div class="success">

            ✓ Successfully scraped:

            <strong>
                {{ data["company_name"] }}
            </strong>

            <br>

            <a
                class="download"
                href="/download"
            >
                Download Excel File
            </a>

        </div>


        <h2>Extracted Information</h2>


        <table>

            <tr>
                <th>Field</th>
                <th>Information</th>
            </tr>


            <tr>
                <td>Company Name</td>
                <td>
                    {% if data["company_name"] %}
                        {{ data["company_name"] }}
                    {% else %}
                        <span class="empty">Not Found</span>
                    {% endif %}
                </td>
            </tr>


            <tr>
                <td>Company Description</td>
                <td>
                    {% if data["company_description"] %}
                        {{ data["company_description"] }}
                    {% else %}
                        <span class="empty">Not Found</span>
                    {% endif %}
                </td>
            </tr>


            <tr>
                <td>Founder / CEO</td>
                <td>
                    {% if data["founder_or_ceo"] %}
                        {{ data["founder_or_ceo"] }}
                    {% else %}
                        <span class="empty">Not Found</span>
                    {% endif %}
                </td>
            </tr>


            <tr>
                <td>Business Email</td>
                <td>
                    {% if data["business_emails"] %}
                        {{ data["business_emails"] }}
                    {% else %}
                        <span class="empty">Not Found</span>
                    {% endif %}
                </td>
            </tr>


            <tr>
                <td>CEO / Founder Email</td>
                <td>
                    {% if data["ceo_or_founder_email"] %}
                        {{ data["ceo_or_founder_email"] }}
                    {% else %}
                        <span class="empty">Not Found</span>
                    {% endif %}
                </td>
            </tr>


            <tr>
                <td>Phone Number</td>
                <td>
                    {% if data["phone_numbers"] %}
                        {{ data["phone_numbers"] }}
                    {% else %}
                        <span class="empty">Not Found</span>
                    {% endif %}
                </td>
            </tr>


            <tr>
                <td>LinkedIn</td>
                <td>
                    {% if data["linkedin"] %}
                        {{ data["linkedin"] }}
                    {% else %}
                        <span class="empty">Not Found</span>
                    {% endif %}
                </td>
            </tr>


            <tr>
                <td>Facebook</td>
                <td>
                    {% if data["facebook"] %}
                        {{ data["facebook"] }}
                    {% else %}
                        <span class="empty">Not Found</span>
                    {% endif %}
                </td>
            </tr>


            <tr>
                <td>Instagram</td>
                <td>
                    {% if data["instagram"] %}
                        {{ data["instagram"] }}
                    {% else %}
                        <span class="empty">Not Found</span>
                    {% endif %}
                </td>
            </tr>


            <tr>
                <td>Twitter</td>
                <td>
                    {% if data["twitter"] %}
                        {{ data["twitter"] }}
                    {% else %}
                        <span class="empty">Not Found</span>
                    {% endif %}
                </td>
            </tr>


            <tr>
                <td>Other Social Links</td>
                <td>
                    {% if data["other_social_links"] %}
                        {{ data["other_social_links"] }}
                    {% else %}
                        <span class="empty">Not Found</span>
                    {% endif %}
                </td>
            </tr>


        </table>

    {% endif %}


</div>


</body>

</html>
"""


# ============================================================
# NORMALIZE SCRAPEGRAPHAI RESULT
# ============================================================

def normalize_result(result):

    print()
    print("==========================================")
    print("RAW SCRAPER RESULT")
    print("==========================================")
    print(result)
    print("==========================================")


    # --------------------------------------------------------
    # CASE 1:
    # ScrapeGraphAI directly returns a dictionary
    # --------------------------------------------------------

    if isinstance(result, dict):

        # Sometimes the actual extracted data is inside
        # a "content" key.

        if "content" in result:

            content = result["content"]

            # Content may itself be a dictionary
            if isinstance(content, dict):

                return content

            # Or content may be JSON text
            if isinstance(content, str):

                try:

                    parsed = json.loads(content)

                    if isinstance(parsed, dict):

                        return parsed

                except json.JSONDecodeError:

                    pass


        # Otherwise the result itself is our data

        return result


    # --------------------------------------------------------
    # CASE 2:
    # ScrapeGraphAI returns JSON string
    # --------------------------------------------------------

    if isinstance(result, str):

        try:

            parsed = json.loads(result)

            if isinstance(parsed, dict):

                return parsed

        except json.JSONDecodeError:

            pass


    # --------------------------------------------------------
    # Nothing usable
    # --------------------------------------------------------

    raise ValueError(
        "Could not understand the format returned by ScrapeGraphAI."
    )


# ============================================================
# HOME
# ============================================================

@app.route("/", methods=["GET"])
def home():

    return render_template_string(
        HTML,
        data=None,
        error=None,
        url=""
    )


# ============================================================
# SCRAPE
# ============================================================

@app.route("/scrape", methods=["POST"])
def scrape():

    url = request.form.get(
        "url",
        ""
    ).strip()


    # --------------------------------------------------------
    # Validate URL
    # --------------------------------------------------------

    if not url:

        return render_template_string(
            HTML,
            data=None,
            error="Please enter a website URL.",
            url=url
        )


    # --------------------------------------------------------
    # Add HTTPS
    # --------------------------------------------------------

    if not url.startswith(
        ("http://", "https://")
    ):

        url = "https://" + url


    try:

        print()
        print("==========================================")
        print("STARTING SCRAPER")
        print("==========================================")
        print("URL:", url)
        print("==========================================")
        print()


        # ====================================================
        # PROMPT
        # ====================================================

        prompt = """

        Extract company information from this website.

        Return ONLY valid structured JSON using exactly
        this structure:

        {
            "company_name": "",
            "company_description": "",
            "founder_or_ceo": "",
            "business_emails": [],
            "ceo_or_founder_email": "",
            "phone_numbers": [],
            "social_media_links": {
                "linkedin": "",
                "facebook": "",
                "instagram": "",
                "twitter": "",
                "other": []
            }
        }


        IMPORTANT RULES:

        - Extract information only from the website.

        - Never guess information.

        - Never generate information.

        - Never invent email addresses.

        - Never invent phone numbers.

        - Never invent social media URLs.

        - Only include publicly listed business emails.

        - Only include a CEO/founder email if the
          website publicly lists it.

        - Extract publicly listed company phone numbers.

        - Extract publicly listed social media links.

        - If information is unavailable, use:
          "" for text fields
          [] for list fields.

        - Return exactly one JSON object.

        """


        # ====================================================
        # CREATE GRAPH
        # ====================================================

        graph = SmartScraperGraph(

            prompt=prompt,

            source=url,

            config=graph_config

        )


        # ====================================================
        # RUN SCRAPER
        # ====================================================

        result = graph.run()


        # ====================================================
        # NORMALIZE RESULT
        # ====================================================

        data = normalize_result(result)


        print()
        print("==========================================")
        print("NORMALIZED DATA")
        print("==========================================")
        print(data)
        print("==========================================")
        print()


        # ====================================================
        # SOCIAL MEDIA
        # ====================================================

        social = data.get(
            "social_media_links",
            {}
        )


        if not isinstance(
            social,
            dict
        ):

            social = {}


        # ====================================================
        # EXTRACT VALUES
        # ====================================================

        company_name = str(
            data.get(
                "company_name",
                ""
            ) or ""
        )


        company_description = str(
            data.get(
                "company_description",
                ""
            ) or ""
        )


        founder_or_ceo = str(
            data.get(
                "founder_or_ceo",
                ""
            ) or ""
        )


        ceo_or_founder_email = str(
            data.get(
                "ceo_or_founder_email",
                ""
            ) or ""
        )


        # ====================================================
        # EMAILS
        # ====================================================

        emails = data.get(
            "business_emails",
            []
        )


        if isinstance(
            emails,
            list
        ):

            business_emails = ", ".join(
                str(email)
                for email in emails
            )

        else:

            business_emails = str(
                emails or ""
            )


        # ====================================================
        # PHONE NUMBERS
        # ====================================================

        phones = data.get(
            "phone_numbers",
            []
        )


        if isinstance(
            phones,
            list
        ):

            phone_numbers = ", ".join(
                str(phone)
                for phone in phones
            )

        else:

            phone_numbers = str(
                phones or ""
            )


        # ====================================================
        # SOCIAL LINKS
        # ====================================================

        linkedin = str(
            social.get(
                "linkedin",
                ""
            ) or ""
        )


        facebook = str(
            social.get(
                "facebook",
                ""
            ) or ""
        )


        instagram = str(
            social.get(
                "instagram",
                ""
            ) or ""
        )


        twitter = str(
            social.get(
                "twitter",
                ""
            ) or ""
        )


        other = social.get(
            "other",
            []
        )


        if isinstance(
            other,
            list
        ):

            other_social_links = ", ".join(
                str(link)
                for link in other
            )

        else:

            other_social_links = str(
                other or ""
            )


        # ====================================================
        # DEBUG
        # ====================================================

        print("==========================================")
        print("EXTRACTED VALUES")
        print("==========================================")

        print("Company Name:", company_name)

        print(
            "Description:",
            company_description
        )

        print(
            "Founder / CEO:",
            founder_or_ceo
        )

        print(
            "Business Email:",
            business_emails
        )

        print(
            "CEO Email:",
            ceo_or_founder_email
        )

        print(
            "Phone:",
            phone_numbers
        )

        print(
            "LinkedIn:",
            linkedin
        )

        print(
            "Facebook:",
            facebook
        )

        print(
            "Instagram:",
            instagram
        )

        print(
            "Twitter:",
            twitter
        )

        print(
            "Other:",
            other_social_links
        )

        print("==========================================")
        print()


        # ====================================================
        # DATA FOR BROWSER
        # ====================================================

        browser_data = {

            "company_name":
                company_name,

            "company_description":
                company_description,

            "founder_or_ceo":
                founder_or_ceo,

            "business_emails":
                business_emails,

            "ceo_or_founder_email":
                ceo_or_founder_email,

            "phone_numbers":
                phone_numbers,

            "linkedin":
                linkedin,

            "facebook":
                facebook,

            "instagram":
                instagram,

            "twitter":
                twitter,

            "other_social_links":
                other_social_links
        }


        # ====================================================
        # CREATE EXCEL
        # ====================================================

        workbook = Workbook()


        sheet = workbook.active


        sheet.title = "Company Leads"


        # ====================================================
        # HEADERS
        # ====================================================

        headers = [

            "Company Name",

            "Company Description",

            "Founder / CEO",

            "Business Email",

            "CEO / Founder Email",

            "Phone Number",

            "LinkedIn",

            "Facebook",

            "Instagram",

            "Twitter",

            "Other Social Links"

        ]


        sheet.append(headers)


        # ====================================================
        # HEADER FORMATTING
        # ====================================================

        for cell in sheet[1]:

            cell.font = Font(
                bold=True
            )

            cell.alignment = Alignment(
                horizontal="center",
                vertical="center"
            )


        # ====================================================
        # ADD DATA
        # ====================================================

        sheet.append([

            company_name,

            company_description,

            founder_or_ceo,

            business_emails,

            ceo_or_founder_email,

            phone_numbers,

            linkedin,

            facebook,

            instagram,

            twitter,

            other_social_links

        ])


        # ====================================================
        # COLUMN WIDTHS
        # ====================================================

        widths = {

            "A": 25,

            "B": 70,

            "C": 25,

            "D": 30,

            "E": 30,

            "F": 25,

            "G": 45,

            "H": 35,

            "I": 45,

            "J": 35,

            "K": 50

        }


        for column, width in widths.items():

            sheet.column_dimensions[
                column
            ].width = width


        # ====================================================
        # WRAP TEXT
        # ====================================================

        for row in sheet.iter_rows():

            for cell in row:

                cell.alignment = Alignment(

                    wrap_text=True,

                    vertical="top"

                )


        # ====================================================
        # FREEZE HEADER
        # ====================================================

        sheet.freeze_panes = "A2"


        # ====================================================
        # SAVE
        # ====================================================

        workbook.save(
            EXCEL_FILE
        )


        print(
            "Excel saved:",
            os.path.abspath(
                EXCEL_FILE
            )
        )


        # ====================================================
        # SHOW RESULT
        # ====================================================

        return render_template_string(

            HTML,

            data=browser_data,

            error=None,

            url=url

        )


    except Exception as e:

        print()
        print("==========================================")
        print("SCRAPER ERROR")
        print("==========================================")
        print(e)
        print("==========================================")
        print()


        return render_template_string(

            HTML,

            data=None,

            error=str(e),

            url=url

        )


# ============================================================
# DOWNLOAD
# ============================================================

@app.route("/download")
def download():

    if not os.path.exists(
        EXCEL_FILE
    ):

        return (
            "Excel file does not exist. "
            "Scrape a website first.",
            404
        )


    return send_file(

        EXCEL_FILE,

        as_attachment=True,

        download_name="scraped_companies.xlsx"

    )


# ============================================================
# START SERVER
# ============================================================

if __name__ == "__main__":

    app.run(

        debug=True,

        host="127.0.0.1",

        port=5000

    )
from google import genai

import os
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

def summarize(news_items: list) -> str:
    """
    Takes a list of {title, link, snippet} dicts.
    Returns a Hebrew digest string ready to send to Telegram.
    """
    content = "\n\n".join([
        f"TITLE: {n['title']}\nSNIPPET: {n['snippet']}\nLINK: {n['link']}"
        for n in news_items[:25]
    ])

    prompt = f"""
אתה עורך חדשות בתחום התעופה, הקרוזים והתיירות.

המשימה שלך:
1. קבץ כתבות שמתארות את אותו סיפור (אל תכלול כפילויות).
2. לכל קבוצת סיפורים צור:
   - כותרת קצרה ומושכת בעברית
   - סיכום של 2–3 משפטים בעברית, בגוף שלישי, בטון מקצועי אך קריא
3. הצג את הכותרות המקוריות עם הקישורים כמקורות.
4. הגבל ל-15 סיפורים מרכזיים לכל היותר.
5. סיים עם שורה קצרה כמו: "יום טוב ומוצלח! 🚢"

פורמט לכל סיפור:

🚢 *<כותרת בעברית>*
<סיכום בעברית>

מקורות:
- <כותרת מקורית> — <קישור>

---

נתונים:
{content}
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
    )

    return response.text

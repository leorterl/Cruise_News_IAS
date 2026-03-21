import requests
from bs4 import BeautifulSoup

URLS = [
    "https://cruiseindustrynews.com/cruise-news/",
    "https://cruisefever.net/",
    "https://www.royalcaribbeanblog.com/",
    "https://www.travelweekly.com/Cruise-Travel",
]

def extract_links(url):
    try:
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        results = []

        for a in soup.find_all("a", href=True):
            title = a.get_text(strip=True)
            link = a["href"]

            if len(title) > 40 and link.startswith("http"):
                results.append({"title": title, "link": link})

        return results[:10]

    except:
        return []

def get_snippet(url):
    try:
        r = requests.get(url, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")

        p = soup.find("p")
        return p.get_text()[:200] if p else ""

    except:
        return ""

def collect():
    data = []

    for site in URLS:
        items = extract_links(site)

        for item in items:
            snippet = get_snippet(item["link"])
            data.append({
                "title": item["title"],
                "link": item["link"],
                "snippet": snippet
            })

    return data

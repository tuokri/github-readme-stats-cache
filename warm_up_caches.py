from concurrent.futures import ThreadPoolExecutor
from pprint import pprint
from urllib.parse import urlparse
from urllib.parse import urlunparse

import bs4
import requests


def main() -> None:
    page = requests.get("https://github.com/tuokri/tuokri").text
    soup = bs4.BeautifulSoup(page, "html.parser")
    imgs = soup.find_all("img", attrs={"data-canonical-src": True})
    urls = set(
        urlunparse(urlparse(img["data-canonical-src"])) for img in imgs
    )
    print("sending requests to the following urls:")
    pprint(urls)
    with ThreadPoolExecutor() as executor:
        results = executor.map(requests.get, urls, timeout=60.0)
    resps = [result for result in results]
    print(f"got {len(resps)} responses: {resps}")


if __name__ == "__main__":
    main()

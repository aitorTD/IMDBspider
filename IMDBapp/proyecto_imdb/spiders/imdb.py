import scrapy
import json
import re
from scrapy.http import Request


class ImdbSpider(scrapy.Spider):
    """Scrapy spider that extracts the IMDb Top 250 chart.

    Why this approach:
    - IMDb changes its HTML/CSS classes frequently, which makes purely CSS-based extraction brittle.
    - The chart page includes a JSON-LD `<script type="application/ld+json">` with an ItemList of movies.
      JSON-LD is meant for machines (SEO / structured data) and tends to be more stable.

    Output:
    - This spider yields plain Python dicts (Scrapy items) so they can be exported with `-O output.json`.
    """

    name = 'imdb'
    allowed_domains = ['www.imdb.com']
    initial_url = 'https://www.imdb.com'
    start_urls = ['https://www.imdb.com/es-es/chart/top/?ref_=hm_nv_menu']
    indice = 0

    def parse(self, response):
        """Parse the chart page and yield one record per movie.

        We build a mapping `tconst -> rank` from the HTML links.
        This is important because users may sort/filter the page; the *visual chart rank* (#1..#250)
        is encoded in `ref_=chttp_t_<rank>`.
        """

        # Extract IMDb's canonical chart rank from the anchor URLs.
        # Example: /title/tt0111161/?ref_=chttp_t_1
        rank_by_tconst = {}
        for href in response.css('a.ipc-title-link-wrapper::attr(href)').getall():
            m = re.search(r'/title/(tt\d+)/\?ref_=chttp_t_(\d+)', href)
            if not m:
                continue
            rank_by_tconst[m.group(1)] = int(m.group(2))

        # Parse the JSON-LD structured data.
        # We look for the ItemList node and iterate its `itemListElement`.
        ld_json_texts = response.css('script[type="application/ld+json"]::text').getall()
        data = None
        for txt in ld_json_texts:
            try:
                candidate = json.loads(txt)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and candidate.get('@type') == 'ItemList' and 'itemListElement' in candidate:
                data = candidate
                break

        if not data:
            return

        for i, element in enumerate(data.get('itemListElement', []), start=1):
            item = element.get('item') if isinstance(element, dict) else None
            if not isinstance(item, dict):
                continue

            # JSON-LD embeds rating info in a nested object.
            aggregate = item.get('aggregateRating') if isinstance(item.get('aggregateRating'), dict) else {}
            url = item.get('url')
            tconst_match = re.search(r'/title/(tt\d+)/', url) if isinstance(url, str) else None
            tconst = tconst_match.group(1) if tconst_match else None
            rank = rank_by_tconst.get(tconst) if tconst else None

            # Yield a dict. Scrapy will export this using whatever feed format you choose.
            yield {
                'rank': rank if rank is not None else i,
                'url': url,
                'name': item.get('name'),
                'alternateName': item.get('alternateName'),
                'description': item.get('description'),
                'image': item.get('image'),
                'ratingValue': aggregate.get('ratingValue'),
                'ratingCount': aggregate.get('ratingCount'),
                'contentRating': item.get('contentRating'),
                'genre': item.get('genre'),
                'duration': item.get('duration'),
            }


    def films(self, response):
        titulo = response.css('h1::text').get()
        directores = response.css("ul.sc-bfec09a1-8 li:nth-child(1) li a::text").getall()
        escritores = response.css("ul.sc-bfec09a1-8 li:nth-child(2) li a::text").getall()
        actores = response.css(".sc-bfec09a1-7 > a::text").getall()
        resenas = response.css("ul.sc-3ff39621-0 .score::text").getall()

        print(response.meta.get('puesto'), '-',titulo, '-', directores, '-', escritores, '-', actores, '-', resenas)

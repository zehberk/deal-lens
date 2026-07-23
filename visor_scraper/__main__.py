"""Compatibility module for the legacy ``python -m visor_scraper`` command."""

from visor_scraper.scraper import main

if __name__ == "__main__":  # pragma: no cover
    main()

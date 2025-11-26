## Setup
```
python3 -m venv venv
source venv/bin/activate
```

```
pip freeze > requirements.txt
pip install -r requirements.txt
```

## Usage
Run normally (uses config.ini):
```
python scrape_router_devices.py --config config.ini

python router_scraper.py --headless (add --password, --debug, or --driver-path)
```

Override password on the command line:

```
python router_scraper.py --password mySecretPass
```

Force headless mode (for cron jobs):
```
python router_scraper.py --headless
```
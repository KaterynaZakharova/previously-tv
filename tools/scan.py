"""Report playback and recap readiness without making any AI request."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.catalog import Catalog,ROOT
from backend.media_catalog import MediaCatalog
if __name__=='__main__':
    if (ROOT/'assets/db.sqlite').is_file():
        for m in MediaCatalog(ROOT).library():
            for e in m['episodes']:print(m['title'],e['order'],'PLAYBACK READY' if e['ready'] else 'PLAYBACK MISSING','· RECAP READY' if e['recap_ready'] else '· TRANSCRIPT NOT SUPPLIED' if not e['transcript'] else '· PREPARE MEDIA')
    else:
        for card in Catalog().cards():print(card['id'],'READY' if card['inputs_ready'] else 'MISSING INPUTS',card['input_message'])

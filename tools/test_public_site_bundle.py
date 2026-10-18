import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

import curated_events as c
import public_site_bundle as bundle
from seed_event_registry import seed
from test_curated_home import sample_item

class PublicSiteTests(unittest.TestCase):
    def test_allowlist_rejects_private_and_traversal_paths(self):
        for name in ('../.env','briefing/sent_history.json','news/2026-10-18.db','jobs.sqlite3','output/foo.txt','.local-refresh/events.sqlite3'):
            self.assertFalse(bundle.allowed(name))
        for name in ('index.html','daily/2026-10-18.html','weibo/latest.json'):
            self.assertTrue(bundle.allowed(name))
        with self.assertRaises(ValueError):bundle.validate('weibo/latest.json',b'{"api_token":"not-public"}')
        with self.assertRaises(ValueError):bundle.validate('index.html',b'SQLite format 3\x00')

    def test_tar_traversal_and_symlink_rejected_before_writes(self):
        for member in ('../escaped.html','index.html'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);archive=root/'bad.tar.gz'
                with tarfile.open(archive,'w:gz') as data:
                    info=tarfile.TarInfo(member)
                    if member=='index.html':info.type=tarfile.SYMTYPE;info.linkname='../outside'
                    data.addfile(info)
                with self.assertRaises(ValueError):bundle.restore(archive,root/'out')
                self.assertFalse((root/'out').exists())

    def test_cloud_registry_seed_preserves_event_and_progress_ids(self):
        items=[sample_item('Swisse抗糖丸被检出铅超标'),sample_item('Swisse客服回应抗糖丸铅超标：相关批次未进入中国销售')]
        initial=c.cluster_home_events(items)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'public';path=Path(directory)/'private/events.sqlite3'
            c.write_json(root/'source-pool/home-events.json',c.home_event_snapshot(initial,len(items),{}, {}, {}))
            self.assertTrue(seed(root,path)['seeded'])
            self.assertFalse(seed(root,path)['seeded'])
            repeated=c.cluster_home_events(items,registry_path=path)
            self.assertEqual(initial[0].key,repeated[0].key)
            self.assertEqual({p.key for p in initial[0].progresses},{p.key for p in repeated[0].progresses})

    def test_cloud_bootstrap_preserves_published_closed_day_scores(self):
        from datetime import datetime,timedelta
        now=datetime.now(c.CHINA);previous=(now-timedelta(days=1)).strftime('%Y-%m-%d')
        item=sample_item('黄金期货价格上涨');item.published_dt=now-timedelta(days=1)
        events=c.cluster_home_events([item])
        with tempfile.TemporaryDirectory() as directory:
            original=Path(directory)/'original';cloud=Path(directory)/'cloud'
            c.render_site_daily(events,original,now=now)
            data=json.loads((original/f'daily/{previous}.json').read_text())
            c.write_json(cloud/f'daily/{previous}.json',data)
            c.render_site_daily([],cloud,now=now)
            restored=json.loads((cloud/f'daily/{previous}.json').read_text())
            self.assertEqual([e['id'] for e in data['events']],[e['id'] for e in restored['events']])
            self.assertEqual([e['score'] for e in data['events']],[e['score'] for e in restored['events']])

    def test_bundle_roundtrip_does_not_include_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);out=root/'output';out.mkdir()
            events=c.cluster_home_events([sample_item('英伟达发布AI芯片')])
            c.render_site_daily(events,out)
            (out/'index.html').write_text('<html>public</html>')
            (out/'hotlists').mkdir();(out/'hotlists/index.html').write_text('source')
            c.write_json(out/'weibo/latest.json',{'ok':True,'items':[]})
            c.write_json(out/'source-pool/home-events.json',c.home_event_snapshot(events,1,{}, {}, {}))
            (out/'briefing').mkdir();(out/'briefing/sent_history.json').write_text('{"private":true}')
            (out/'private.db').write_bytes(b'SQLite format 3\x00')
            result=bundle.pack(out,root/'deploy',root/'public.tar.gz')
            self.assertGreater(result['files'],20)
            bundle.restore(root/'public.tar.gz',root/'restored')
            self.assertEqual((out/'index.html').read_bytes(),(root/'restored/index.html').read_bytes())
            self.assertFalse((root/'restored/briefing').exists())
            self.assertFalse((root/'restored/private.db').exists())

if __name__=='__main__':unittest.main()

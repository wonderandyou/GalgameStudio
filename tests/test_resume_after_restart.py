"""修「重启后端就丢失断点续跑能力」和「放宽分支层级不该清空图」。

实测背景（2026-11-08，主人项目「了」）：
  后端重启后 self.jobs 是空的 → previous=None → retry=False → resume=False
  → skeleton() 把 7 章 135 节点清空，从第一章重写。
  根因：reap_orphans 只改 .jobs 文件的状态，没把 job 读回内存。
"""

import json
import pathlib
import tempfile
import unittest

from app.mock_api import Jobs, resume_signature


def job_body(node_count=488, depth=2, requirement='一个秋天的凌晨'):
    return {'requirement': requirement,
            'params': {'node_count': node_count, 'branch_depth': depth,
                       'key_branches': 2, 'flavor_branches': 8, 'resolution': [1920, 1080]}}


class ResumeSignatureTests(unittest.TestCase):
    def test_branch_depth_does_not_block_resume(self):
        """放宽层级（2 → 3）不该被当成「换了配置要重写」。"""
        self.assertEqual(resume_signature(job_body(depth=2)), resume_signature(job_body(depth=3)))

    def test_node_count_still_blocks_resume(self):
        """节点数变了还是要重写：预算对不上，不能接。"""
        self.assertNotEqual(resume_signature(job_body(node_count=488)),
                            resume_signature(job_body(node_count=200)))

    def test_requirement_change_blocks_resume(self):
        self.assertNotEqual(resume_signature(job_body(requirement='另一个故事')),
                            resume_signature(job_body()))


class ReapOrphansTests(unittest.TestCase):
    def test_jobs_are_reloaded_into_memory(self):
        """重启后必须能从磁盘找回 job —— generate() 判 resume 只看 self.jobs。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            folder = root / '.jobs'
            folder.mkdir()
            older = dict(id='j_old', project_id='p_x', kind='generate', body=job_body(),
                         status='failed', mock_mode=False, events=[{'x': 'y' * 5000}])
            running = dict(id='j_run', project_id='p_x', kind='generate', body=job_body(),
                           status='running', mock_mode=False, events=[])
            (folder / 'j_old.json').write_text(json.dumps(older), encoding='utf-8')
            (folder / 'j_run.json').write_text(json.dumps(running), encoding='utf-8')

            jobs = Jobs.__new__(Jobs)          # 只跑 reap_orphans，不碰其他初始化
            jobs.root = root
            jobs.jobs = {}
            jobs.reap_orphans()

            self.assertIn('j_old', jobs.jobs, '失败的旧任务必须回到内存，否则续跑永远失效')
            self.assertEqual(jobs.jobs['j_old']['status'], 'failed')
            self.assertEqual(jobs.jobs['j_old']['body'], job_body())
            # 重启后遗留的 running 要标成 failed
            self.assertIn('j_run', jobs.jobs)
            self.assertEqual(jobs.jobs['j_run']['status'], 'failed')
            self.assertEqual(jobs.jobs['j_run']['error']['code'], 'INTERRUPTED')
            # events 不能跟着进内存（一个就几十万字符）
            self.assertEqual(jobs.jobs['j_old']['events'], [])

            # 文件里也要落成 failed，否则下次启动又会当成 running
            on_disk = json.loads((folder / 'j_run.json').read_text(encoding='utf-8'))
            self.assertEqual(on_disk['status'], 'failed')


if __name__ == '__main__':
    unittest.main()

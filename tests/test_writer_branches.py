import unittest
from collections import Counter

from app.agents.loop import _act_budgets, _branch_depth, _chapter_choice_counts, _mock_layout


def layout_nodes(budget, choices, depth, last):
    nodes = []
    for index, (kind, targets, ending) in enumerate(_mock_layout(budget, choices, depth, last)):
        if targets is None:
            targets = (index + 1,) if index + 1 < budget else ()
        nodes.append(dict(id=str(index), type=kind, end_kind=ending,
                          choices=[dict(to=str(target)) for target in targets]))
    return nodes


class WriterBranchTests(unittest.TestCase):
    def test_long_book_distributes_choices_independent_of_depth(self):
        chapters = [dict(node_budget=budget) for act in _act_budgets(1000) for budget in act]
        shallow = _chapter_choice_counts(chapters, 1000, 1)
        nested = _chapter_choice_counts(chapters, 1000, 2)
        self.assertEqual(shallow, nested)
        self.assertTrue(80 <= sum(nested) <= 150)
        self.assertEqual(set(nested), {1, 2})

    def test_reconvergence_resets_depth_without_reducing_choices(self):
        shallow = layout_nodes(16, 2, 1, last=False)
        nested = layout_nodes(16, 2, 2, last=False)
        for nodes in (shallow, nested):
            self.assertEqual(len(nodes), 16)
            self.assertEqual(Counter(node['type'] for node in nodes), {'scene': 14, 'choice': 2})
            self.assertEqual([node['id'] for node in nodes if not node['choices']], ['15'])
        self.assertEqual(_branch_depth(shallow), 1)
        self.assertEqual(_branch_depth(nested), 2)

    def test_zero_depth_keeps_a_linear_route_to_the_true_ending(self):
        counts = _chapter_choice_counts([dict(node_budget=15)], 16, 0)
        self.assertEqual(counts, [0])
        nodes = layout_nodes(15, counts[0], 0, last=True)
        self.assertEqual(_branch_depth(nodes), 0)
        self.assertEqual([node['end_kind'] for node in nodes if node['type'] == 'ending'], ['true'])
        self.assertTrue(all(len(node['choices']) == 1 for node in nodes[:-1]))


if __name__ == '__main__':
    unittest.main()

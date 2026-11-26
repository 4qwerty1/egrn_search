from lookup_support import PRIVATE_OWNERSHIP, LookupCase, filename, positive


FIRST_ADDRESS = "Первый"
FIRST_ACCOUNT = "101"
FIRST_NUMBER = "1:2:3:4"


def _results_without_form(*, after_reload=False):
    return {
        **positive(FIRST_NUMBER),
        "hideSearchForm": True,
        "hideSearchFormOnReload": after_reload,
    }


class MissingSearchFormTests(LookupCase):
    def test_missing_search_form_recovers(self):
        self._assert_search_resumes(_results_without_form(after_reload=True))

    def test_missing_object_type_hint_recovers(self):
        self._assert_search_resumes({**positive(FIRST_NUMBER), "hideObjectTypeHint": True})

    def test_missing_search_form_shares_limit(self):
        second = "Без выдачи"
        self.write_addresses((FIRST_ACCOUNT, FIRST_ADDRESS), (102, second), (103, "Следующий"))
        status, stderr = self.lookup({FIRST_ADDRESS: _results_without_form()}, timeout_ms=1_000)
        self.assertEqual(status, 1)
        self.assertIn(second, stderr)
        self.assertEqual(self.activity.submissions, [FIRST_ADDRESS, second, second])
        expected = (FIRST_ACCOUNT, FIRST_ADDRESS, "Да", PRIVATE_OWNERSHIP, filename(FIRST_ADDRESS))
        self.assertEqual(self.rows()[1:], [expected])

    def _assert_search_resumes(self, first_scenario):
        account, second = "104", "Второй"
        self.write_addresses((FIRST_ACCOUNT, FIRST_ADDRESS), (account, second))
        status, stderr = self.lookup({
            FIRST_ADDRESS: first_scenario,
            second: positive("1:2:3:7"),
        }, timeout_ms=1_000)
        self.assertEqual(status, 0, stderr)
        self.assertEqual(self.activity.submissions, [FIRST_ADDRESS, second])
        self.assertEqual(self.rows()[1:], [
            (FIRST_ACCOUNT, FIRST_ADDRESS, "Да", PRIVATE_OWNERSHIP, filename(FIRST_ADDRESS)),
            (account, second, "Да", PRIVATE_OWNERSHIP, filename(second)),
        ])

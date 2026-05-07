import os
import sys
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from downloader import (
    sanitize_filename, is_image_type, get_month_from_time,
    parse_record, commodity_type_name,
)


class TestSanitizeFilename:
    def test_removes_invalid_chars(self):
        assert sanitize_filename('现代简约客厅*?') == '现代简约客厅'

    def test_strips_whitespace(self):
        assert sanitize_filename('  现代简约客厅  ') == '现代简约客厅'

    def test_removes_slashes(self):
        assert sanitize_filename('a/b\\c:d<e>f"g|h*i?j') == 'abcdefghij'


class TestIsImageType:
    def test_texture_is_image(self):
        assert is_image_type(2) is True

    def test_model_is_not_image(self):
        for t in [0, 3, 4, 5, 8, 20]:
            assert is_image_type(t) is False


class TestCommodityTypeName:
    def test_known_types(self):
        assert commodity_type_name(0) == '3d'
        assert commodity_type_name(2) == 'tietu'
        assert commodity_type_name(3) == '3d'
        assert commodity_type_name(4) == 'su'
        assert commodity_type_name(5) == 'sgt'
        assert commodity_type_name(8) == 'ziliaoku'
        assert commodity_type_name(20) == 'wenben'

    def test_unknown_type(self):
        assert commodity_type_name(99) == 'other'


class TestGetMonthFromTime:
    def test_extracts_month(self):
        assert get_month_from_time('2026-04-28 10:58:39') == '2026-04'
        assert get_month_from_time('2026-05-01 00:00:00') == '2026-05'

    def test_empty_string(self):
        assert get_month_from_time('') == ''


class TestParseRecord:
    def test_parse_valid_record(self):
        item = {
            'id': 123,
            'skuid': '1195169820',
            'commodityType': 0,
            'accountId': '19004605301512',
            'nickName': 'caojuntao',
            'createTime': '2026-04-28 10:58:39',
            'goldAmount': 1300,
        }
        result = parse_record(item)
        assert result['model_id'] == '1195169820'
        assert result['model_name'] == '1195169820'
        assert result['month'] == '2026-04'
        assert result['commodity_type'] == 0

    def test_parse_missing_fields(self):
        item = {'skuid': '123', 'createTime': '2026-05-01'}
        result = parse_record(item)
        assert result['model_id'] == '123'
        assert result['month'] == '2026-05'

# Dataset Card: report_catalog

**Provider**: [Cninfo (巨潮资讯网)](https://www.cninfo.com.cn/)
**API**: `hisAnnouncement/query`

A-share periodic report announcement catalog (annual, semiannual, Q1, Q3 reports) with PDF download links.

## Metadata

- **Grain**: one announcement
- **Primary key**: `source`, `announcement_id`
- **Date field**: `announcement_date`
- **Partition**: by `universe_id`

## Notes

Cninfo rate-limits aggressively (2s minimum between requests). The crawler respects `--fake` guard (omit `--fake` for real access). Report versioning (`is_correction`, `version_no`, `latest_version`) tracks original vs. revised filings.

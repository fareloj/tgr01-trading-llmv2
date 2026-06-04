## 2026-06-04 - Prevent XXE Vulnerability
**Vulnerability:** Insecure XML Parsing
**Learning:** Python's built-in `xml.etree.ElementTree` is vulnerable to XXE attacks when parsing untrusted XML, such as RSS feeds from the internet.
**Prevention:** Use `defusedxml.ElementTree` as a drop-in replacement whenever parsing untrusted XML.

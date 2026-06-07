## 2026-06-07 - pd.concat vs np.maximum for DataFrame column-wise operations
**Learning:** Using `pd.concat([a, b, c], axis=1).max(axis=1)` on Pandas Series is extremely slow for computing element-wise maximums across multiple series, even small ones. It has significant overhead from concatenating and indexing.
**Action:** Use `np.maximum(a, np.maximum(b, c))` for a vast performance improvement (about 2x faster). Also, using `np.where(condition, series, default)` instead of `series.where(condition, default)` can be much faster as well.

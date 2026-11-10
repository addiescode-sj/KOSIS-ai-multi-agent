"""services/: 통계 계산·차트·원자료 접근 (연구 문서 6장). 숫자는 여기(실전 1 포함)의 코드가 계산한다.

    statistics.py         실전 1 summarize_stat_run을 감싼 summarize_stat 툴
    chart.py              ChartSpec → Vega-Lite (노드용 함수 + make_chart 툴)
    parquet_repository.py 표 ID → tidy DataFrame (T3-17 Fetch & Normalize, docs/data-contract.md)
"""

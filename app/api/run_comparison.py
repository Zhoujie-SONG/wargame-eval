from fastapi import APIRouter, Query
from fastapi.responses import Response

from app.services import run_comparison as comparison


router = APIRouter(prefix='/api/run-comparisons')


@router.get('/candidates')
def candidates():
    return comparison.candidates()


@router.get('')
def compare(run_id: list[str] = Query(default=[])):
    return comparison.compare(run_id)


@router.get('/charts/{chart}.svg')
def chart(chart: str, run_id: list[str] = Query(default=[])):
    if chart not in ('radar', 'ranking'):
        raise ValueError('仅支持雷达图 radar 和综合分图 ranking')
    data = comparison.compare(run_id)
    return Response(comparison.chart_svg(data, chart), media_type='image/svg+xml', headers={
        'Content-Disposition': f'inline; filename=run-comparison-{chart}.svg',
        'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    })

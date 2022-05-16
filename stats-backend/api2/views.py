from django.shortcuts import render
from .models import Node, Offer
from .serializers import NodeSerializer, OfferSerializer
import redis
import json
import aioredis
import requests

from django.http import JsonResponse, HttpResponse

pool = redis.ConnectionPool(host='redis', port=6379, db=0)
r = redis.Redis(connection_pool=pool)


def globe_data(request):
    # open json file and return data
    with open('/globe_data.geojson') as json_file:
        data = json.load(json_file)
    return JsonResponse(data, safe=False, json_dumps_params={'indent': 4})


async def cheapest_provider(request):
    if request.method == 'GET':
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("v2_cheapest_provider")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={'indent': 4})
    else:
        return HttpResponse(status=400)


def node(request, yagna_id):
    if request.method == 'GET':
        if yagna_id.startswith("0x"):
            data = Node.objects.filter(node_id=yagna_id)
            if data:
                serializer = NodeSerializer(data, many=True)
                return JsonResponse(serializer.data, safe=False, json_dumps_params={'indent': 4})
            else:
                return HttpResponse(status=404)
        else:
            return HttpResponse(status=404)
    else:
        return HttpResponse(status=400)


async def network_online(request):
    if request.method == 'GET':
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("v2_online")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={'indent': 4})
    else:
        return HttpResponse(status=400)

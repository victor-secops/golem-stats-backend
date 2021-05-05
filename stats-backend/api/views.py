from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .utils import get_stats_data, get_yastats_data
import os
import time
from collector.models import Node
from .serializers import NodeSerializer
from django.shortcuts import render
from django.db.models import Count
from django.conf import settings
import asyncio
import redis
import json
import aioredis
from asgiref.sync import sync_to_async


from django.http import JsonResponse, HttpResponse


@sync_to_async
def get_node(yagna_id):
    data = Node.objects.filter(node_id=yagna_id)
    return data


@sync_to_async
def get_node_by_wallet(wallet):
    data = Node.objects.filter(wallet=wallet)
    if data:
        return data
    else:
        return None


async def online_nodes(request):
    """
    List all online nodes.
    """
    if request.method == 'GET':
        r = await aioredis.create_redis_pool('redis://redis:6379/0')
        content = await r.get("online", encoding='utf-8')
        data = json.loads(content)
        r.close()
        await r.wait_closed()
        return JsonResponse(data, safe=False, json_dumps_params={'indent': 4})
    else:
        return HttpResponse(status=400)


async def activity_graph_provider(request, yagna_id):
    end = round(time.time())
    start = end - 86400
    domain = os.environ.get(
        'STATS_URL') + f'api/datasources/proxy/40/api/v1/query_range?query=sum(changes(activity_provider_usage_0%7Bjob%3D~"community.1"%2C%20instance%3D~"{yagna_id}"%7D%5B60s%5D))&start={start}&end={end}&step=120'
    data = await get_yastats_data(domain)
    return JsonResponse(data)


async def payments_last_n_hours_provider(request, yagna_id, hours):
    now = round(time.time())
    domain = os.environ.get(
        'STATS_URL') + f'api/datasources/proxy/40/api/v1/query?query=sum(increase(payment_amount_received%7Bhostname%3D~"{yagna_id}"%2C%20job%3D~"community.1"%7D%5B{hours}h%5D)%2F10%5E9)&time={now}'
    data = await get_yastats_data(domain)
    content = {'earnings': data['data']
               ['result'][0]['value'][1]}
    return JsonResponse(content)


async def provider_computing(request, yagna_id):
    now = round(time.time())
    domain = os.environ.get(
        'STATS_URL') + f'api/datasources/proxy/40/api/v1/query?query=activity_provider_created%7Bhostname%3D~"0xe2462fa49d20324b799b2894bb03fd021489df3a"%2C%20job%3D~"community.1"%7D%20-%20activity_provider_destroyed%7Bhostname%3D~"{yagna_id}"%2C%20job%3D~"community.1"%7D&time={now}'
    data = await get_yastats_data(domain)
    print(data)
    content = {'computing': data['data']
               ['result'][0]['value'][1]}
    return JsonResponse(content)


async def node(request, yagna_id):
    """
    Retrieves data about a specific node.
    """
    if request.method == 'GET':
        data = await get_node(yagna_id)
        serializer = NodeSerializer(data, many=True)
        return JsonResponse(serializer.data, safe=False)
    else:
        return HttpResponse(status=400)


async def node_wallet(request, wallet):
    """
    Returns all the nodes with the specified wallet address.
    """
    if request.method == 'GET':
        data = await get_node_by_wallet(wallet)
        print(data)
        if data != None:
            serializer = NodeSerializer(data, many=True)
            return JsonResponse(serializer.data, safe=False)
        else:
            return HttpResponse(status=404)

    else:
        return HttpResponse(status=400)


async def general_stats(request):
    """
    List network stats for online nodes.
    """
    if request.method == 'GET':
        r = await aioredis.create_redis_pool('redis://redis:6379/0')
        content = await r.get("online_stats")
        data = json.loads(content)
        r.close()
        await r.wait_closed()
        return JsonResponse(data, safe=False)
    else:
        return HttpResponse(status=400)


async def network_utilization(request, start, end):
    """
    Queries the networks utilization from a start date to the end date specified, and returns
    timestamps in ms along with providers computing.
    """
    if request.method == 'GET':
        r = await aioredis.create_redis_pool('redis://redis:6379/0')
        content = await r.get("network_utilization")
        data = json.loads(content)
        r.close()
        await r.wait_closed()
        return JsonResponse(data, safe=False)
    else:
        return HttpResponse(status=400)


async def network_versions(request):
    """
    Queries the networks nodes for their yagna versions
    """
    if request.method == 'GET':
        r = await aioredis.create_redis_pool('redis://redis:6379/0')
        content = await r.get("network_versions")
        data = json.loads(content)
        r.close()
        await r.wait_closed()
        return JsonResponse(data, safe=False)
    else:
        return HttpResponse(status=400)


async def providers_computing_currently(request):
    """
    Returns how many providers are currently computing a task.
    """
    if request.method == 'GET':
        r = await aioredis.create_redis_pool('redis://redis:6379/0')
        content = await r.get("computing_now")
        data = json.loads(content)
        r.close()
        await r.wait_closed()
        return JsonResponse(data, safe=False)
    else:
        return HttpResponse(status=400)


async def providers_average_earnings(request):
    """
    Returns providers average earnings per task in the last hour.
    """
    if request.method == 'GET':
        r = await aioredis.create_redis_pool('redis://redis:6379/0')
        content = await r.get("provider_average_earnings")
        data = json.loads(content)
        r.close()
        await r.wait_closed()
        return JsonResponse(data, safe=False)
    else:
        return HttpResponse(status=400)


async def network_earnings_24h(request):
    """
    Returns the earnings for the whole network the last n hours.
    """
    if request.method == 'GET':
        r = await aioredis.create_redis_pool('redis://redis:6379/0')
        content = await r.get("network_earnings_24h")
        data = json.loads(content)
        r.close()
        await r.wait_closed()
        return JsonResponse(data, safe=False)
    else:
        return HttpResponse(status=400)


async def network_earnings_365d(request):
    """
    Returns the earnings for the whole network the last n hours.
    """
    if request.method == 'GET':
        r = await aioredis.create_redis_pool('redis://redis:6379/0')
        content = await r.get("network_earnings_365d")
        data = json.loads(content)
        r.close()
        await r.wait_closed()
        return JsonResponse(data, safe=False)
    else:
        return HttpResponse(status=400)


async def network_earnings_6h(request):
    """
    Returns the earnings for the whole network the last n hours.
    """
    if request.method == 'GET':
        r = await aioredis.create_redis_pool('redis://redis:6379/0')
        content = await r.get("network_earnings_6h")
        data = json.loads(content)
        r.close()
        await r.wait_closed()
        return JsonResponse(data, safe=False)
    else:
        return HttpResponse(status=400)

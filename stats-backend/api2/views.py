from collector.models import Node as NodeV1
from api.serializers import FlatNodeSerializer
from django.shortcuts import render
from .models import Node, Offer, HealtcheckTask
from .serializers import NodeSerializer, OfferSerializer
import redis
import json
import aioredis
import requests
from .utils import identify_network
from django.http import JsonResponse, HttpResponse

pool = redis.ConnectionPool(host="redis", port=6379, db=0)
r = redis.Redis(connection_pool=pool)

from datetime import timedelta
from typing import List
from .models import Node, NodeStatusHistory, Offer, EC2Instance
from django.utils import timezone


from rest_framework.decorators import api_view
from rest_framework.response import Response


from .models import EC2Instance, Offer, Node
from math import ceil
from .scoring import calculate_uptime_percentage


async def pricing_past_hour(request):
    try:
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        pricing_data = json.loads(await r.get("pricing_past_hour_v2"))
        pool.disconnect()
        return JsonResponse(pricing_data)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


from django.core.paginator import Paginator
from django.http import JsonResponse
from .models import ProviderWithTask


def task_pricing(request):
    try:
        network = request.GET.get("network", "mainnet")
        timeframe = request.GET.get("timeframe", "All")
        page = int(request.GET.get("page", 1))
        per_page = int(request.GET.get("per_page", 10))

        data = (
            ProviderWithTask.objects.filter(network=network)
            .prefetch_related("instance", "offer")
            .select_related("offer__cheaper_than", "offer__overpriced_compared_to")
            .order_by("created_at")
        )

        if timeframe != "All":
            start_date = datetime.now() - timedelta(days=int(timeframe[:-1]))
            data = data.filter(created_at__gte=start_date)

        paginator = Paginator(data, per_page)
        page_data = paginator.get_page(page)

        response_data = {
            "results": [],
            "page": page,
            "per_page": per_page,
            "total_pages": paginator.num_pages,
            "total_results": paginator.count,
        }

        for entry in page_data:
            entry_data = {
                "providerName": entry.offer.properties.get("golem.node.id.name", ""),
                "providerId": entry.instance.node_id,
                "cores": entry.offer.properties.get("golem.inf.cpu.threads", 0),
                "memory": entry.offer.properties.get("golem.inf.mem.gib", 0),
                "disk": entry.offer.properties.get("golem.inf.storage.gib", 0),
                "cpuh": entry.cpu_per_hour,
                "envh": entry.env_per_hour,
                "start": entry.start_price,
                "date": entry.created_at.timestamp(),
            }
            response_data["results"].append(entry_data)

        return JsonResponse(response_data)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


async def list_ec2_instances_comparison(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("ec2_comparison")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


async def online_stats_by_runtime(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("online_stats_by_runtime")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


async def online_stats(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("v2_network_online_stats")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


async def network_historical_stats(request):
    """
    Network stats past 30 minutes.
    """
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("network_historical_stats_v2")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


async def historical_pricing_data(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("pricing_data_charted_v2")
        data = json.loads(content) if content else {}
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


from datetime import datetime


@api_view(["GET"])
def node_uptime(request, yagna_id):
    node = Node.objects.filter(node_id=yagna_id).first()
    if not node:
        return JsonResponse(
            {
                "first_seen": None,
                "data": [],
                "status": "offline",
            },
            status=404,
        )

    statuses = NodeStatusHistory.objects.filter(provider=node).order_by("timestamp")
    response_data = []
    current_time = timezone.now()
    first_seen_date = node.uptime_created_at.date()
    today_date = current_time.date()
    total_days = (today_date - first_seen_date).days + 1  # Including today
    last_offline_timestamp = None

    for day_offset in range(total_days):
        day = first_seen_date + timedelta(days=day_offset)
        day_start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
        day_end = day_start + timedelta(days=1)
        data_points_for_day = statuses.filter(
            timestamp__range=(day_start, day_end)
        ).distinct("timestamp")

        if data_points_for_day.exists():
            online_count = data_points_for_day.filter(is_online=True).count()
            offline_count = data_points_for_day.filter(is_online=False).count()
            if online_count == 0:
                status = "offline"
            elif offline_count == 0:
                status = "online"
            else:
                status = "outage"

            downtime_periods = []
            for point in data_points_for_day:
                if not point.is_online:
                    if last_offline_timestamp is None:
                        last_offline_timestamp = point.timestamp
                else:
                    if last_offline_timestamp is not None:
                        downtime_period = process_downtime(
                            last_offline_timestamp, point.timestamp
                        )
                        downtime_periods.append(downtime_period)
                        last_offline_timestamp = None

            response_data.append(
                {
                    "date": day.strftime("%d %B, %Y"),
                    "status": status,
                    "downtimes": downtime_periods,
                }
            )
        else:
            # Assume the status did not change this day, infer from last known status if available
            last_known_status = statuses.filter(timestamp__lt=day_start).last()
            inferred_status = (
                last_known_status.is_online if last_known_status else False
            )  # default to offline if unknown
            response_data.append(
                {
                    "date": day.strftime("%d %B, %Y"),
                    "status": "online" if inferred_status else "offline",
                    "downtime": None,
                }
            )

    # Handling ongoing downtime
    if last_offline_timestamp is not None:
        ongoing_downtime = process_downtime(last_offline_timestamp, current_time)
        response_data[-1]["downtime"] = ongoing_downtime

    return JsonResponse(
        {
            "first_seen": node.uptime_created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "uptime_percentage": calculate_uptime_percentage(yagna_id, node),
            "data": response_data,
            "current_status": "online" if node.online else "offline",
        }
    )


async def online_nodes_uptime_donut_data(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("online_nodes_uptime_donut_data")
        try:
            data = json.loads(content)
        except TypeError:
            data = {"error": "No data found"}
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


def process_downtime(start_time, end_time):
    duration = (end_time - start_time).total_seconds()
    days, remainder = divmod(duration, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    date_format = "%d %B, %Y"
    down_date = start_time.strftime(date_format)

    parts = []
    if days:
        parts.append(f"{int(days)} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{int(hours)} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{int(minutes)} minute{'s' if minutes != 1 else ''}")
    if seconds or not parts:
        parts.append(f"{int(seconds)} second{'s' if seconds != 1 else ''}")

    human_readable = f"Down for {' and '.join(parts)}"

    time_period = (
        f"From {start_time.strftime('%I:%M %p')} to {end_time.strftime('%I:%M %p')}"
    )

    return {
        "date": down_date,
        "human_period": human_readable,
        "time_period": time_period,
    }


def calculate_time_diff(check_time, granularity, node):
    if granularity >= 86400:
        return f"{(check_time - node.uptime_created_at).days} days ago"
    elif granularity >= 3600:
        hours_ago = int((timezone.now() - check_time).total_seconds() / 3600)
        return f"{hours_ago} hours ago" if hours_ago > 1 else "1 hour ago"
    else:
        minutes_ago = int((timezone.now() - check_time).total_seconds() / 60)
        return f"{minutes_ago} minutes ago" if minutes_ago > 1 else "1 minute ago"


def globe_data(request):
    # open json file and return data
    with open("/globe_data.geojson") as json_file:
        data = json.load(json_file)
    return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})


async def golem_main_website_index(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)

        fetch_blogs = await r.get("v2_index_blog_posts")
        blogs = json.loads(fetch_blogs)

        fetch_network_stats = await r.get("online_stats")
        stats = json.loads(fetch_network_stats)

        fetch_cheapest_providers = await r.get("v2_cheapest_provider")
        cheapest_providers = json.loads(fetch_cheapest_providers)

        pool.disconnect()
        return JsonResponse(
            {"blogs": blogs, "stats": stats, "providers": cheapest_providers},
            safe=False,
            json_dumps_params={"indent": 4},
        )
    else:
        return HttpResponse(status=400)


def node_wallet(request, wallet):
    if request.method != "GET":
        return HttpResponse(status=400)

    try:
        reputation_response = requests.get(
            "https://reputation.dev-test.golem.network/v2/providers/scores"
        )
        reputation_response.raise_for_status()
    except requests.RequestException:
        return HttpResponse(status=500)

    external_data = reputation_response.json()
    success_rate_mapping = {
        provider["provider"]["id"]: provider["scores"]["successRate"]
        for provider in external_data.get("testedProviders", [])
    }

    blacklist_provider_mapping = {
        provider["provider"]["id"]: provider["reason"]
        for provider in external_data.get("rejectedProviders", [])
    }

    blacklist_operator_mapping = {
        operator["operator"]["walletAddress"]: operator["reason"]
        for operator in external_data.get("rejectedOperators", [])
    }

    data = Node.objects.filter(wallet=wallet)
    if not data.exists():
        return HttpResponse(status=404)

    serializer = NodeSerializer(data, many=True)
    serialized_data = serializer.data

    default_reputation = {
        "blacklisted": False,
        "blacklistedReason": None,
        "taskReputation": None,
    }
    for node in serialized_data:
        node_id = node["node_id"]
        node_wallet = node.get("wallet")
        node["reputation"] = default_reputation.copy()

        if node_id in blacklist_provider_mapping:
            node["reputation"].update(
                {
                    "blacklisted": True,
                    "blacklistedReason": blacklist_provider_mapping[node_id],
                }
            )
        elif node_wallet in blacklist_operator_mapping:
            node["reputation"].update(
                {
                    "blacklisted": True,
                    "blacklistedReason": blacklist_operator_mapping[node_wallet],
                }
            )

        if node_id in success_rate_mapping:
            node["reputation"]["taskReputation"] = success_rate_mapping[node_id] * 100

    return JsonResponse(serialized_data, safe=False, json_dumps_params={"indent": 4})


def node(request, yagna_id):
    if request.method == "GET":
        if yagna_id.startswith("0x"):
            data = Node.objects.filter(node_id=yagna_id)
            if data:
                serializer = NodeSerializer(data, many=True)
                return JsonResponse(
                    serializer.data, safe=False, json_dumps_params={"indent": 4}
                )
            else:
                return HttpResponse(status=404)
        else:
            return HttpResponse(status=404)
    else:
        return HttpResponse(status=400)


async def online_nodes(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("v2_online_counts")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


async def cpu_vendor_stats(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("cpu_vendors_count")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


async def cpu_architecture_stats(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("cpu_architecture_count")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


from collector.models import Requestors
from .models import RelayNodes


def get_transfer_sum(request, node_id, epoch):
    try:
        epoch_now = int(timezone.now().timestamp())
        url = f"http://erc20-api/erc20/api/stats/transfers?chain=137&receiver={node_id}&from={epoch}&to={epoch_now}"
        response = requests.get(url)
        if response.status_code != 200:
            return JsonResponse({"error": "Failed to get data from API"}, status=500)
        data = response.json()

        transfers = data.get("transfers", [])
        from_addrs = {t["fromAddr"] for t in transfers}

        matched_addrs = set(
            Requestors.objects.filter(node_id__in=from_addrs).values_list(
                "node_id", flat=True
            )
        )
        matched_addrs.update(
            RelayNodes.objects.filter(node_id__in=from_addrs).values_list(
                "node_id", flat=True
            )
        )

        total_amount_wei_matched = 0
        total_amount_wei_not_matched = 0
        for t in transfers:

            amount = int(t["tokenAmount"])
            if t["fromAddr"] in matched_addrs:
                print(f"Matched Transfer Amount: {amount / 1e18} ETH")
                total_amount_wei_matched += amount
            else:
                total_amount_wei_not_matched += amount

        return JsonResponse(
            {
                "total_amount_matched": total_amount_wei_matched / 1e18,
                "total_amount_not_matched": total_amount_wei_not_matched / 1e18,
            }
        )
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


async def network_online(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("v2_online")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


async def network_online_new_stats_page(request):
    try:
        page = int(request.GET.get("page", 1))
        size = int(request.GET.get("size", 30))
        runtime = request.GET.get("runtime", None)
        runtime_key_suffix = f"_{runtime}" if runtime else ""
    except ValueError:
        return HttpResponse(status=400, content="Invalid page or size parameter")

    if request.method != "GET":
        return HttpResponse(status=400)

    pool = aioredis.ConnectionPool.from_url(
        "redis://redis:6379/0", decode_responses=True
    )
    r = aioredis.Redis(connection_pool=pool)
    content = await r.get(f"v2_online_{page}_{size}{runtime_key_suffix}")
    metadata_content = await r.get(f"v2_online_metadata{runtime_key_suffix}")

    if not content or not metadata_content:
        return HttpResponse(
            status=404, content="Cache not found for specified page and size"
        )

    data = json.loads(content)
    metadata = json.loads(metadata_content)
    response_data = {"data": data, "metadata": metadata}
    return JsonResponse(response_data, safe=False, json_dumps_params={"indent": 4})


async def network_online_flatmap(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("v2_online_flatmap")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


def cheapest_by_cores(request):
    """Displays an array of cheapest offers by number of cores that are NOT computing a task right now"""
    cores = {}
    for i in range(256):
        cores[f"cores_{i}"] = []
    req = requests.get(
        "https://api.coingecko.com/api/v3/coins/ethereum/contract/0x7DD9c5Cba05E151C895FDe1CF355C9A1D5DA6429"
    )
    data = req.json()
    price = data["market_data"]["current_price"]["usd"]
    obj = Offer.objects.filter(
        provider__online=True, runtime="vm", provider__computing_now=False
    ).order_by("monthly_price_glm")
    serializer = OfferSerializer(obj, many=True)
    mainnet_providers = []
    for index, provider in enumerate(serializer.data):
        print(provider["properties"])
        if (
            "golem.com.payment.platform.erc20-mainnet-glm.address"
            in provider["properties"]
        ):
            print("TRUEEEE")
            mainnet_providers.append(provider)
    sorted_pricing_and_specs = sorted(
        mainnet_providers,
        key=lambda element: (
            float(element["properties"]["golem.inf.cpu.threads"]),
            float(element["monthly_price_glm"]),
        ),
    )
    for obj in sorted_pricing_and_specs:
        provider = {}
        provider["name"] = obj["properties"]["golem.node.id.name"]
        provider["id"] = obj["properties"]["id"]
        provider["usd_monthly"] = float(price) * float(obj["monthly_price_glm"])
        provider["cores"] = float(obj["properties"]["golem.inf.cpu.threads"])
        provider["memory"] = float(obj["properties"]["golem.inf.mem.gib"])
        provider["disk"] = float(obj["properties"]["golem.inf.storage.gib"])
        provider["glm"] = float(obj["monthly_price_glm"])
        cores_int = int(obj["properties"]["golem.inf.cpu.threads"])
        cores[f"cores_{cores_int}"].append(provider)

    for i in range(256):
        cores[f"cores_{i}"] = sorted(
            cores[f"cores_{i}"], key=lambda element: element["usd_monthly"]
        )
    return JsonResponse(cores, safe=False, json_dumps_params={"indent": 4})


async def cheapest_offer(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("v2_cheapest_offer")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from web3 import Web3
from .tasks import healthcheck_provider


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def verify_provider_is_working(request):
    node_id = request.data.get("node_id")
    try:
        provider = Node.objects.get(node_id=node_id)
    except Node.DoesNotExist:
        return Response(
            {"error": "Provider not found."}, status=status.HTTP_404_NOT_FOUND
        )
    if node_id is None:
        return Response(
            {"error": "node_id is required"}, status=status.HTTP_400_BAD_REQUEST
        )

    checksum_address_user = Web3.to_checksum_address(
        request.user.userprofile.wallet_address
    )
    checksum_address_provider = Web3.to_checksum_address(provider.wallet)
    if checksum_address_user != checksum_address_provider:
        return Response(
            {"error": "Provider does not belong to this user."},
            status=status.HTTP_403_FORBIDDEN,
        )
    else:
        find_network = identify_network(provider)
        if find_network == "mainnet":
            network = "polygon"
        else:
            network = "goerli"
        obj = HealtcheckTask.objects.create(
            provider=provider,
            user=request.user.userprofile,
            status="The Healthcheck has been scheduled to queue. We will start in a moment.",
        )

        healthcheck_provider.delay(node_id, network, obj.id)
        return Response(
            {"taskId": obj.id, "status": "success"},
            status=status.HTTP_200_OK,
        )


@api_view(["POST"])
def healthcheck_status(request):
    task_status = request.data.get("status")
    task_id = request.data.get("taskId")
    try:
        obj = HealtcheckTask.objects.get(id=task_id)
        obj.status = task_status
        obj.save()
        return Response({"status": "ok"}, status=status.HTTP_200_OK)

    except HealtcheckTask.DoesNotExist:
        return Response(
            {"error": "Healthcheck task not found."}, status=status.HTTP_404_NOT_FOUND
        )


@api_view(["POST"])
def get_healthcheck_status(request):
    task_id = request.data.get("taskId")
    try:
        obj = HealtcheckTask.objects.get(id=task_id)
        return Response({"status": obj.status}, status=status.HTTP_200_OK)

    except HealtcheckTask.DoesNotExist:
        return Response(
            {"error": "Healthcheck task not found."}, status=status.HTTP_404_NOT_FOUND
        )


from .models import GolemTransactions

from django.db.models import Sum, Q, FloatField
from django.db.models.functions import TruncDay, Coalesce

from django.http import JsonResponse


async def daily_volume_golem_vs_chain(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("daily_volume_golem_vs_chain")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


from django.db.models import Count


async def transaction_volume_over_time(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("transaction_volume_over_time")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


async def amount_transferred_over_time(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("amount_transferred_over_time")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


async def transaction_type_comparison(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("transaction_type_comparison")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


from django.db.models import IntegerField, ExpressionWrapper, Case, When, Avg


async def daily_transaction_type_counts(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("daily_transaction_type_counts")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


async def average_transaction_value_over_time(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("average_transaction_value_over_time")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


async def computing_total_over_time(request):
    if request.method == "GET":
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("computing_total_over_time")
        data = json.loads(content)
        pool.disconnect()
        return JsonResponse(data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)


from django.http import JsonResponse, HttpResponse
import json
import aioredis


async def wallets_and_ids(request):
    if request.method == "GET":
        query = request.GET.get(
            "query", ""
        ).lower()  # Get the query parameter from the request
        pool = aioredis.ConnectionPool.from_url(
            "redis://redis:6379/0", decode_responses=True
        )
        r = aioredis.Redis(connection_pool=pool)
        content = await r.get("wallets_and_ids")
        data = json.loads(content)

        # Filter logic based on query
        filtered_data = {"wallets": [], "providers": []}
        for item in data.get("wallets", []):
            if query in item.get("address", "").lower():
                filtered_data["wallets"].append(item)

        for item in data.get("providers", []):
            if (
                query in item.get("provider_name", "").lower()
                or query in str(item.get("id", "")).lower()
            ):
                filtered_data["providers"].append(item)

        pool.disconnect()
        return JsonResponse(filtered_data, safe=False, json_dumps_params={"indent": 4})
    else:
        return HttpResponse(status=400)

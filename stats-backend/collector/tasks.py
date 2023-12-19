import requests
from django.db.models import DateField
from django.db.models.functions import TruncDay
from core.celery import app
from celery import Celery
import json
import subprocess
import os
import statistics
from api.utils import get_stats_data
import time
import redis
from django.conf import settings
from datetime import datetime, timedelta, date
from .models import (
    Node,
    NetworkStats,
    NetworkStatsMax,
    ProvidersComputing,
    NetworkAveragePricing,
    NetworkMedianPricing,
    NetworkAveragePricingMax,
    NetworkMedianPricingMax,
    ProvidersComputingMax,
    Network,
    Requestors,
    requestor_scraper_check,
)
from api2.models import Node as Nodev2
from django.db.models import Max, Avg, Min
from api.models import APIHits
from api.serializers import (
    NodeSerializer,
    NetworkMedianPricingMaxSerializer,
    NetworkAveragePricingMaxSerializer,
    ProvidersComputingMaxSerializer,
    NetworkStatsMaxSerializer,
    NetworkStatsSerializer,
    RequestorSerializer,
)
from django.utils import timezone
import logging

# jsonmsg = {"user_id": elem, "path": "/src/data/user_avatars/" + elem + ".png"}
# r.lpush("image_classifier", json.dumps(jsonmsg))

pool = redis.ConnectionPool(host="redis", port=6379, db=0)
r = redis.Redis(connection_pool=pool)


@app.task
def save_endpoint_logs_to_db():
    length = r.llen("API")
    # Remove entries in list
    r.delete("API")
    obj, objcreated = APIHits.objects.get_or_create(id=1)
    if objcreated:
        obj.count = length
        obj.save()
    else:
        obj.count = obj.count + length
        obj.save()


@app.task
def requests_served():
    obj = APIHits.objects.get(id=1)
    jsondata = {"count": obj.count}
    serialized = json.dumps(jsondata)
    r.set("api_requests", serialized)


@app.task
def requestors_to_redis():
    query = Requestors.objects.all().order_by("-tasks_requested")
    serializer = RequestorSerializer(query, many=True)
    data = json.dumps(serializer.data)
    r.set("requestors", data)


@app.task
def stats_snapshot_yesterday():
    start_date = date.today() - timedelta(days=1)
    date_trunc_day = TruncDay("date", output_field=DateField())

    online = (
        NetworkStats.objects.filter(date__gte=start_date)
        .annotate(day=date_trunc_day)
        .values("day")
        .annotate(online=Max("online"))
    )
    cores = (
        NetworkStats.objects.filter(date__gte=start_date)
        .annotate(day=date_trunc_day)
        .values("day")
        .annotate(cores=Max("cores"))
    )
    memory = (
        NetworkStats.objects.filter(date__gte=start_date)
        .annotate(day=date_trunc_day)
        .values("day")
        .annotate(memory=Max("memory"))
    )
    disk = (
        NetworkStats.objects.filter(date__gte=start_date)
        .annotate(day=date_trunc_day)
        .values("day")
        .annotate(disk=Max("disk"))
    )

    existing_dates = NetworkStatsMax.objects.all().values_list("date", flat=True)

    for online_obj, cores_obj, memory_obj, disk_obj in zip(online, cores, memory, disk):
        current_date = online_obj["day"]
        if current_date not in existing_dates:
            NetworkStatsMax.objects.create(
                online=online_obj["online"],
                cores=cores_obj["cores"],
                memory=memory_obj["memory"],
                disk=disk_obj["disk"],
                date=current_date,
            )


@app.task
def computing_snapshot_yesterday():
    start_date = date.today() - timedelta(days=1)
    date_trunc_day = TruncDay("date", output_field=DateField())

    computing = (
        ProvidersComputing.objects.filter(date__gte=start_date)
        .annotate(day=date_trunc_day)
        .values("day")
        .annotate(total=Max("total"))
    )

    existing_dates = ProvidersComputingMax.objects.all().values_list("date", flat=True)

    for obj in computing:
        if obj["day"] not in existing_dates:
            ProvidersComputingMax.objects.create(total=obj["total"], date=obj["day"])


@app.task
def pricing_snapshot_yesterday():
    start_date = date.today() - timedelta(days=1)
    date_trunc_day = TruncDay("date", output_field=DateField())

    avg_prices = (
        NetworkAveragePricing.objects.filter(date__gte=start_date)
        .annotate(day=date_trunc_day)
        .values("day")
        .annotate(start=Avg("start"), cpuh=Avg("cpuh"), perh=Avg("perh"))
    )
    median_prices = (
        NetworkMedianPricing.objects.filter(date__gte=start_date)
        .annotate(day=date_trunc_day)
        .values("day")
        .annotate(start=Min("start"), cpuh=Min("cpuh"), perh=Min("perh"))
    )

    existing_avg_dates = NetworkAveragePricingMax.objects.all().values_list(
        "date", flat=True
    )
    existing_median_dates = NetworkMedianPricingMax.objects.all().values_list(
        "date", flat=True
    )

    for avg_obj, median_obj in zip(avg_prices, median_prices):
        if avg_obj["day"] not in existing_avg_dates:
            NetworkAveragePricingMax.objects.create(
                start=avg_obj["start"],
                cpuh=avg_obj["cpuh"],
                perh=avg_obj["perh"],
                date=avg_obj["day"],
            )
        if median_obj["day"] not in existing_median_dates:
            NetworkMedianPricingMax.objects.create(
                start=median_obj["start"],
                cpuh=median_obj["cpuh"],
                perh=median_obj["perh"],
                date=median_obj["day"],
            )


@app.task
def network_average_pricing():
    perhour = []
    cpuhour = []
    start = []
    data = Node.objects.filter(online=True)
    for obj in data:
        if (
            str(obj.data["golem.runtime.name"]) == "vm"
            or str(obj.data["golem.runtime.name"]) == "wasmtime"
        ):
            pricing_vector = {
                obj.data["golem.com.usage.vector"][0]: obj.data[
                    "golem.com.pricing.model.linear.coeffs"
                ][0],
                obj.data["golem.com.usage.vector"][1]: obj.data[
                    "golem.com.pricing.model.linear.coeffs"
                ][1],
            }
            if len(str(pricing_vector["golem.usage.duration_sec"])) < 5:
                perhour.append(pricing_vector["golem.usage.duration_sec"])
            else:
                perhour.append(pricing_vector["golem.usage.duration_sec"] * 3600)

                start.append((obj.data["golem.com.pricing.model.linear.coeffs"][2]))
            if len(str(pricing_vector["golem.usage.cpu_sec"])) < 5:
                cpuhour.append(pricing_vector["golem.usage.cpu_sec"])
            else:
                cpuhour.append(pricing_vector["golem.usage.cpu_sec"] * 3600)

    content = {
        "cpuhour": statistics.mean(cpuhour),
        "perhour": statistics.mean(perhour),
        "start": statistics.mean(start),
    }
    serialized = json.dumps(content)
    NetworkAveragePricing.objects.create(
        start=statistics.mean(start),
        cpuh=statistics.mean(cpuhour),
        perh=statistics.mean(perhour),
    )
    r.set("network_average_pricing", serialized)


@app.task
def network_median_pricing():
    perhour = []
    cpuhour = []
    startprice = []
    data = Node.objects.filter(online=True)
    for obj in data:
        if (
            str(obj.data["golem.runtime.name"]) == "vm"
            or str(obj.data["golem.runtime.name"]) == "wasmtime"
        ):
            pricing_vector = {
                obj.data["golem.com.usage.vector"][0]: obj.data[
                    "golem.com.pricing.model.linear.coeffs"
                ][0],
                obj.data["golem.com.usage.vector"][1]: obj.data[
                    "golem.com.pricing.model.linear.coeffs"
                ][1],
            }
            if len(str(pricing_vector["golem.usage.duration_sec"])) < 5:
                perhour.append(pricing_vector["golem.usage.duration_sec"])
            else:
                perhour.append(pricing_vector["golem.usage.duration_sec"] * 3600)

                startprice.append(
                    (obj.data["golem.com.pricing.model.linear.coeffs"][2])
                )
            if len(str(pricing_vector["golem.usage.cpu_sec"])) < 5:
                cpuhour.append(pricing_vector["golem.usage.cpu_sec"])
            else:
                cpuhour.append(pricing_vector["golem.usage.cpu_sec"] * 3600)

    if not perhour:
        return
    if not cpuhour:
        return
    if not startprice:
        return

    content = {
        "cpuhour": statistics.median(cpuhour),
        "perhour": statistics.median(perhour),
        "start": statistics.median(startprice),
    }
    serialized = json.dumps(content)
    NetworkMedianPricing.objects.create(
        start=statistics.median(startprice),
        cpuh=statistics.median(cpuhour),
        perh=statistics.median(perhour),
    )
    r.set("network_median_pricing", serialized)


@app.task
def network_online_to_redis():
    data = Node.objects.filter(online=True)
    serializer = NodeSerializer(data, many=True)
    test = json.dumps(serializer.data)
    r.set("online", test)


@app.task
def max_stats():
    data = ProvidersComputingMax.objects.all()
    serializercomputing = ProvidersComputingMaxSerializer(data, many=True)
    providermax = json.dumps(serializercomputing.data)
    r.set("providers_computing_max", providermax)

    data2 = NetworkAveragePricingMax.objects.all()
    serializeravg = NetworkAveragePricingMaxSerializer(data2, many=True)
    avgmax = json.dumps(serializeravg.data)
    r.set("pricing_average_max", avgmax)

    data3 = NetworkMedianPricingMax.objects.all()
    serializermedian = NetworkMedianPricingMaxSerializer(data3, many=True)
    medianmax = json.dumps(serializermedian.data)
    r.set("pricing_median_max", medianmax)

    data4 = NetworkStatsMax.objects.all()
    serializerstats = NetworkStatsMaxSerializer(data4, many=True)
    statsmax = json.dumps(serializerstats.data)
    r.set("stats_max", statsmax)


@app.task
def network_stats_to_redis():
    cores = []
    threads = []
    memory = []
    disk = []
    query = Node.objects.filter(online=True)
    for obj in query:
        cores.append(obj.data["golem.inf.cpu.cores"])
        threads.append(obj.data["golem.inf.cpu.threads"])
        memory.append(obj.data["golem.inf.mem.gib"])
        disk.append(obj.data["golem.inf.storage.gib"])
    content = {
        "online": len(query),
        "cores": sum(cores),
        "threads": sum(threads),
        "memory": sum(memory),
        "disk": sum(disk),
    }

    mainnet = query.filter(
        data__has_key="golem.com.payment.platform.erc20-mainnet-glm.address"
    )
    testnet = query.exclude(
        data__has_key="golem.com.payment.platform.erc20-mainnet-glm.address"
    )

    content["mainnet"] = mainnet.count()
    content["testnet"] = testnet.count()
    serialized = json.dumps(content)
    NetworkStats.objects.create(
        online=len(query), cores=sum(threads), memory=sum(memory), disk=sum(disk)
    )

    r.set("online_stats", serialized)


@app.task
def networkstats_30m():
    now = datetime.now()
    before = now - timedelta(minutes=30)
    data = NetworkStats.objects.filter(date__range=(before, now)).order_by("date")
    serializer = NetworkStatsSerializer(data, many=True)
    r.set("stats_30m", json.dumps(serializer.data))


@app.task
def network_utilization_to_redis():
    end = round(time.time())
    start = end - 21600
    domain = (
        os.environ.get("STATS_URL")
        + f"api/datasources/proxy/40/api/v1/query_range?query=sum(activity_provider_created%7Bjob%3D~%22community.1%22%7D%20-%20activity_provider_destroyed%7Bjob%3D~%22community.1%22%7D)&start={start}&end={end}&step=30"
    )
    content = get_stats_data(domain)
    if content[1] == 200:
        serialized = json.dumps(content[0])
        r.set("network_utilization", serialized)


@app.task
def network_node_versions():
    now = round(time.time())
    domain = (
        os.environ.get("STATS_URL")
        + f'api/datasources/proxy/40/api/v1/query?query=yagna_version_major%7Bjob%3D"community.1"%7D*100%2Byagna_version_minor%7Bjob%3D"community.1"%7D*10%2Byagna_version_patch%7Bjob%3D"community.1"%7D&time={now}'
    )
    data = get_stats_data(domain)
    nodes = data[0]["data"]["result"]
    for obj in nodes:
        try:
            node = obj["metric"]["instance"]
            if len(obj["value"][1]) == 2:
                version = "0" + obj["value"][1]
                concatinated = version[0] + "." + version[1] + "." + version[2]
                Node.objects.filter(node_id=node).update(version=concatinated)
                Nodev2.objects.filter(node_id=node).update(version=concatinated)
            elif len(obj["value"][1]) == 3:
                version = obj["value"][1]
                concatinated = "0." + version[0] + version[1] + "." + version[2]
                Node.objects.filter(node_id=node).update(version=concatinated)
                Nodev2.objects.filter(node_id=node).update(version=concatinated)
        except Exception as e:
            print(e)
            continue


@app.task
def network_versions_to_redis():
    now = round(time.time())
    domain = (
        os.environ.get("STATS_URL")
        + f'api/datasources/proxy/40/api/v1/query_range?query=count_values("version"%2C%20yagna_version_major%7Bjob%3D"community.1"%7D*100%2Byagna_version_minor%7Bjob%3D"community.1"%7D*10%2Byagna_version_patch%7Bjob%3D"community.1"%7D)&start={now}&end={now}&step=5'
    )
    content = get_stats_data(domain)
    if content[1] == 200:
        versions_nonsorted = []
        versions = []
        data = content[0]["data"]["result"]
        # Append to array so we can sort
        for obj in data:
            versions_nonsorted.append(
                {"version": int(obj["metric"]["version"]), "count": obj["values"][0][1]}
            )
        versions_nonsorted.sort(key=lambda x: x["version"], reverse=False)
        for obj in versions_nonsorted:
            version = str(obj["version"])
            count = obj["count"]
            if len(version) == 2:
                concatinated = "0." + version[0] + "." + version[1]
            elif len(version) == 3:
                concatinated = "0." + version[0] + version[1] + "." + version[2]
            versions.append(
                {
                    "version": concatinated,
                    "count": count,
                }
            )
        serialized = json.dumps(versions)
        r.set("network_versions", serialized)


def get_earnings(platform, hours):
    end = round(time.time())
    domain = (
        os.environ.get("STATS_URL") + f"api/datasources/proxy/40/api/v1/query?query="
        f'sum(increase(payment_amount_received%7Bjob%3D~"community.1"%2C%20platform%3D"{platform}"%7D%5B{hours}%5D)%2F10%5E9)&time={end}'
    )
    data = get_stats_data(domain)
    if data[1] == 200 and data[0]["data"]["result"]:
        return round(float(data[0]["data"]["result"][0]["value"][1]), 2)
    return 0.0


@app.task
def network_earnings(hours):
    # Platforms to check
    platforms = settings.GOLEM_MAINNET_PAYMENT_DRIVERS

    # Calculating earnings for each platform
    total_earnings = sum(get_earnings(platform, hours) for platform in platforms)

    content = {"total_earnings": round(total_earnings, 2)}
    serialized = json.dumps(content)

    # Assuming 'r' is a Redis connection
    r = redis.Redis(host="redis", port=6379, db=0)
    r.set(f"network_earnings_{hours}", serialized)


@app.task
def fetch_yagna_release():
    url = "https://api.github.com/repos/golemfactory/yagna/releases"
    headers = {"Accept": "application/vnd.github.v3+json"}
    releases_info = []

    while url:
        response = requests.get(url, headers=headers)
        releases = response.json()
        for release in releases:
            if not release["prerelease"]:
                release_data = {
                    "tag_name": release["tag_name"],
                    "published_at": release["published_at"],
                }
                releases_info.append(release_data)
        if "next" in response.links:
            url = response.links["next"]["url"]
        else:
            url = None

    serialized = json.dumps(releases_info)
    r.set("yagna_releases", serialized)


@app.task
def network_total_earnings():
    end = round(time.time())
    network_types = settings.GOLEM_MAINNET_PAYMENT_DRIVERS

    for network in network_types:
        domain = (
            os.environ.get("STATS_URL")
            + f'api/datasources/proxy/40/api/v1/query?query=sum(increase(payment_amount_received%7Bjob%3D~"community.1"%2C%20platform%3D"{network}"%7D%5B2m%5D)%2F10%5E9)&time={end}'
        )
        process_data(domain)


def process_data(domain):
    data = get_stats_data(domain)
    if data[1] == 200 and data[0]["data"]["result"]:
        network_value = float(data[0]["data"]["result"][0]["value"][1])
        if network_value > 0:
            db, created = Network.objects.get_or_create(id=1)
            db.total_earnings = (
                network_value
                if created or db.total_earnings is None
                else db.total_earnings + network_value
            )
            db.save()
            content = {"total_earnings": db.total_earnings}
            serialized = json.dumps(content)
            r.set("network_earnings_90d", serialized)


@app.task
def computing_now_to_redis():
    end = round(time.time())
    start = round(time.time()) - int(10)
    domain = (
        os.environ.get("STATS_URL")
        + f"api/datasources/proxy/40/api/v1/query_range?query=sum(activity_provider_created%7Bjob%3D~%22community.1%22%7D%20-%20activity_provider_destroyed%7Bjob%3D~%22community.1%22%7D)&start={start}&end={end}&step=1"
    )
    data = get_stats_data(domain)
    if data[1] == 200:
        if data[0]["data"]["result"]:
            content = {"computing_now": data[0]["data"]["result"][0]["values"][-1][1]}
            ProvidersComputing.objects.create(
                total=data[0]["data"]["result"][0]["values"][-1][1]
            )
            serialized = json.dumps(content)
            r.set("computing_now", serialized)


@app.task
def providers_average_earnings_to_redis():
    platforms = settings.GOLEM_MAINNET_PAYMENT_DRIVERS

    end = round(time.time())
    total_average_earnings = 0.0

    for platform in platforms:
        domain = (
            os.environ.get("STATS_URL")
            + f'api/datasources/proxy/40/api/v1/query?query=avg(increase(payment_amount_received%7Bjob%3D~"community.1"%2C%20platform%3D"{platform}"%7D%5B24h%5D)%2F10%5E9)&time={end}'
        )
        data = get_stats_data(domain)
        if data[1] == 200 and data[0]["data"]["result"]:
            platform_average = round(float(data[0]["data"]["result"][0]["value"][1]), 4)
        else:
            platform_average = 0.0
        total_average_earnings += platform_average

    content = {"average_earnings": total_average_earnings}
    serialized = json.dumps(content)
    r.set("provider_average_earnings", serialized)


@app.task
def paid_invoices_1h():
    end = round(time.time())
    domain = (
        os.environ.get("STATS_URL")
        + f'api/datasources/proxy/40/api/v1/query?query=sum(increase(payment_invoices_provider_paid%7Bjob%3D~"community.1"%7D%5B1h%5D))%2Fsum(increase(payment_invoices_provider_sent%7Bjob%3D~"community.1"%7D%5B1h%5D))&time={end}'
    )
    data = get_stats_data(domain)
    if data[1] == 200:
        if data[0]["data"]["result"]:
            content = {
                "percentage_paid": float(data[0]["data"]["result"][0]["value"][1]) * 100
            }
            serialized = json.dumps(content)
            r.set("paid_invoices_1h", serialized)


@app.task
def provider_accepted_invoices_1h():
    end = round(time.time())
    domain = (
        os.environ.get("STATS_URL")
        + f'api/datasources/proxy/40/api/v1/query?query=sum(increase(payment_invoices_provider_accepted%7Bjob%3D~"community.1"%7D%5B1h%5D))%2Fsum(increase(payment_invoices_provider_sent%7Bjob%3D~"community.1"%7D%5B1h%5D))&time={end}'
    )
    data = get_stats_data(domain)
    if data[1] == 200:
        if data[0]["data"]["result"]:
            content = {
                "percentage_invoice_accepted": float(
                    data[0]["data"]["result"][0]["value"][1]
                )
                * 100
            }
            serialized = json.dumps(content)
            r.set("provider_accepted_invoice_percentage", serialized)


@app.task
def online_nodes_computing():
    end = round(time.time())
    start = end - 60
    providers = Node.objects.filter(online=True)
    computing_node_ids = []

    for node in providers:
        url = f"api/datasources/proxy/40/api/v1/query_range?query=sum(changes(activity_provider_created%7Bjob%3D~%22community.1%22%2C%20instance%3D~%22{node.node_id}%22%7D[60m]))&start={start}&end={end}&step=30"
        domain = os.environ.get("STATS_URL") + url
        data = get_stats_data(domain)
        if (
            data[1] == 200
            and data[0]["status"] == "success"
            and data[0]["data"]["result"]
        ):
            values = data[0]["data"]["result"][0]["values"]
            if values[-1][1] != "0":
                computing_node_ids.append(node.pk)

    Node.objects.filter(pk__in=computing_node_ids).update(computing_now=True)
    Node.objects.exclude(pk__in=computing_node_ids).update(computing_now=False)


def get_earnings_for_node_on_platform(user_node_id, platform):
    now = round(time.time())
    domain = (
        os.environ.get("STATS_URL")
        + f'api/datasources/proxy/40/api/v1/query?query=sum(increase(payment_amount_received%7Bhostname%3D~"{user_node_id}"%2C%20platform%3D"{platform}"%7D%5B10m%5D)%2F10%5E9)&time={now}'
    )
    data = get_stats_data(domain)
    if data[0]["data"]["result"]:
        return round(float(data[0]["data"]["result"][0]["value"][1]), 2)
    else:
        return 0.0


@app.task
def node_earnings_total(node_version):
    if node_version == "v1":
        providers = Node.objects.filter(online=True)
    elif node_version == "v2":
        providers = Nodev2.objects.filter(online=True)

    for user in providers:
        earnings_total = sum(
            get_earnings_for_node_on_platform(user.node_id, platform)
            for platform in settings.GOLEM_MAINNET_PAYMENT_DRIVERS
        )

        user.earnings_total = (
            user.earnings_total + earnings_total
            if user.earnings_total
            else earnings_total
        )
        user.save(update_fields=["earnings_total"])


@app.task
def market_agreement_termination_reasons():
    end = round(time.time())
    start = round(time.time()) - int(10)
    content = {}
    domain_success = (
        os.environ.get("STATS_URL")
        + f'api/datasources/proxy/40/api/v1/query?query=sum(increase(market_agreements_provider_terminated_reason%7Bjob%3D"community.1"%2C%20reason%3D"Success"%7D%5B1h%5D))&time={end}'
    )
    data_success = get_stats_data(domain_success)
    if data_success[1] == 200:
        if data_success[0]["data"]["result"]:
            content["market_agreements_success"] = round(
                float(data_success[0]["data"]["result"][0]["value"][1])
            )
    # Failure
    domain_cancelled = (
        os.environ.get("STATS_URL")
        + f'api/datasources/proxy/40/api/v1/query?query=sum(increase(market_agreements_provider_terminated_reason%7Bjob%3D"community.1"%2C%20reason%3D"Cancelled"%7D%5B6h%5D))&time={end}'
    )
    data_cancelled = get_stats_data(domain_cancelled)
    if data_cancelled[1] == 200:
        if data_cancelled[0]["data"]["result"]:
            content["market_agreements_cancelled"] = round(
                float(data_cancelled[0]["data"]["result"][0]["value"][1])
            )
    # Expired
    domain_expired = (
        os.environ.get("STATS_URL")
        + f'api/datasources/proxy/40/api/v1/query?query=sum(increase(market_agreements_provider_terminated_reason%7Bjob%3D"community.1"%2C%20reason%3D"Expired"%7D%5B6h%5D))&time={end}'
    )
    data_expired = get_stats_data(domain_expired)
    if data_expired[1] == 200:
        if data_expired[0]["data"]["result"]:
            content["market_agreements_expired"] = round(
                float(data_expired[0]["data"]["result"][0]["value"][1])
            )
    # RequestorUnreachable
    domain_unreachable = (
        os.environ.get("STATS_URL")
        + f'api/datasources/proxy/40/api/v1/query?query=sum(increase(market_agreements_provider_terminated_reason%7Bjob%3D"community.1"%2C%20reason%3D"RequestorUnreachable"%7D%5B6h%5D))&time={end}'
    )
    data_unreachable = get_stats_data(domain_unreachable)
    if data_unreachable[1] == 200:
        if data_unreachable[0]["data"]["result"]:
            content["market_agreements_requestorUnreachable"] = round(
                float(data_unreachable[0]["data"]["result"][0]["value"][1])
            )

    # DebitNotesDeadline
    domain_debitdeadline = (
        os.environ.get("STATS_URL")
        + f'api/datasources/proxy/40/api/v1/query?query=sum(increase(market_agreements_provider_terminated_reason%7Bjob%3D"community.1"%2C%20reason%3D"DebitNotesDeadline"%7D%5B6h%5D))&time={end}'
    )
    data_debitdeadline = get_stats_data(domain_debitdeadline)
    if data_debitdeadline[1] == 200:
        if data_debitdeadline[0]["data"]["result"]:
            content["market_agreements_debitnoteDeadline"] = round(
                float(data_debitdeadline[0]["data"]["result"][0]["value"][1])
            )
    serialized = json.dumps(content)
    r.set("market_agreement_termination_reasons", serialized)


@app.task
def requestor_scraper():
    checker, checkcreated = requestor_scraper_check.objects.get_or_create(id=1)
    if checkcreated:
        # No requestors indexed before, we loop back over the last 90 days to init the table with data.
        checker.indexed_before = True
        checker.save()
        now = round(time.time())
        ninetydaysago = round(time.time()) - int(7776000)
        hour = 3600
        while ninetydaysago < now:
            domain = (
                os.environ.get("STATS_URL")
                + f'api/datasources/proxy/40/api/v1/query?query=increase(market_agreements_requestor_approved%7Bjob%3D"community.1"%7D%5B{hour}s%5D)&time={ninetydaysago+hour}'
            )
            data = get_stats_data(domain)
            ninetydaysago += hour
            if data[1] == 200:
                if data[0]["data"]["result"]:
                    for node in data[0]["data"]["result"]:
                        stats_tasks_requested = float(node["value"][1])
                        if stats_tasks_requested > 1:
                            obj, created = Requestors.objects.get_or_create(
                                node_id=node["metric"]["instance"]
                            )
                            if created:
                                obj.tasks_requested = stats_tasks_requested
                                obj.save()
                            else:
                                obj.tasks_requested = (
                                    obj.tasks_requested + stats_tasks_requested
                                )
                                obj.save()
    else:
        # Already indexed, we check the last 10 seconds.
        now = round(time.time())
        domain = (
            os.environ.get("STATS_URL")
            + f'api/datasources/proxy/40/api/v1/query?query=increase(market_agreements_requestor_approved%7Bjob%3D"community.1"%7D%5B10s%5D)&time={now}'
        )
        data = get_stats_data(domain)
        if data[1] == 200:
            if data[0]["data"]["result"]:
                for node in data[0]["data"]["result"]:
                    stats_tasks_requested = float(node["value"][1])
                    if stats_tasks_requested > 1:
                        obj, created = Requestors.objects.get_or_create(
                            node_id=node["metric"]["instance"]
                        )
                        if created:
                            obj.tasks_requested = stats_tasks_requested
                            obj.save()
                        else:
                            obj.tasks_requested = (
                                obj.tasks_requested + stats_tasks_requested
                            )
                            obj.save()


@app.task
def offer_scraper():
    try:
        os.chdir("/stats-backend/yapapi/examples/low-level-api")

        with open("data.config") as f:
            command = f.readline().strip()

        subprocess.run(command, shell=True, capture_output=True, text=True, check=True)
        content = r.get("v1_offers")
        serialized_content = json.loads(content) if content else []

        serialized_ids = {json.loads(offer)["id"] for offer in serialized_content}
        recent_nodes_qs = Node.objects.filter(
            updated_at__gte=timezone.now() - timezone.timedelta(hours=1)
        )
        recent_nodes = {node.node_id: node for node in recent_nodes_qs}

        nodes_to_update, nodes_to_create = [], []

        for node_id in recent_nodes:
            node = recent_nodes[node_id]
            online = False
            if node_id in serialized_ids:
                node_data = next(
                    (
                        json.loads(offer)
                        for offer in serialized_content
                        if json.loads(offer)["id"] == node_id
                    ),
                    None,
                )
                if node_data:
                    node.data = node_data
                    node.wallet = node_data.get("wallet")
                online = True
            node.updated_at = timezone.now()
            online_command = f"yagna net find {node_id}"
            online_proc = subprocess.run(
                online_command, shell=True, capture_output=True, text=True
            )
            node.online = online and "Request failed" not in online_proc.stderr
            nodes_to_update.append(node)

        for offer in serialized_content:
            data = json.loads(offer)
            provider_id = data["id"]
            if provider_id not in recent_nodes:
                online_command = f"yagna net find {provider_id}"
                online_proc = subprocess.run(
                    online_command, shell=True, capture_output=True, text=True
                )
                online = "Request failed" not in online_proc.stderr
                if online:
                    nodes_to_create.append(
                        Node(
                            node_id=provider_id,
                            data=data,
                            wallet=data.get("wallet"),
                            updated_at=timezone.now(),
                            online=online,
                            hybrid=True,
                        )
                    )

        Node.objects.bulk_update(
            nodes_to_update, fields=["data", "wallet", "online", "updated_at"]
        )
        Node.objects.bulk_create(nodes_to_create)

    except Exception as e:
        logging.error(f"An error occurred in offer_scraper: {e}", exc_info=True)

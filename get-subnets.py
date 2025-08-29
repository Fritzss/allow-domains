#!/usr/bin/python3

import ipaddress
import urllib.request
import os
import shutil
import sys

# Проверка версии Python
if sys.version_info < (3, 10):
    print("Требуется Python версии 3.10 или выше")
    sys.exit(1)

BGP_TOOLS_URL = 'https://bgp.tools/table.txt'
HEADERS = {'User-Agent': 'itdog.info - hi@itdog.info'}
AS_FILE = 'AS.lst'
IPv4_DIR = 'Subnets/IPv4'
IPv6_DIR = 'Subnets/IPv6'

# AS номера
AS_META = '32934'
AS_TWITTER = '13414'
AS_HETZNER = '24940'
AS_OVH = '16276'

# Имена файлов
META = 'meta.lst'
TWITTER = 'twitter.lst'
TELEGRAM = 'telegram.lst'
CLOUDFLARE = 'cloudflare.lst'
HETZNER = 'hetzner.lst'
OVH = 'ovh.lst'
DISCORD = 'discord.lst'

# URL-адреса
DISCORD_VOICE_V4 = 'https://iplist.opencck.org/?format=text&data=cidr4&site=discord.gg&site=discord.media'
DISCORD_VOICE_V6 = 'https://iplist.opencck.org/?format=text&data=cidr6&site=discord.gg&site=discord.media'
TELEGRAM_CIDR_URL = 'https://core.telegram.org/resources/cidr.txt'
CLOUDFLARE_V4 = 'https://www.cloudflare.com/ips-v4'
CLOUDFLARE_V6 = 'https://www.cloudflare.com/ips-v6'

def create_directories():
    """Создает необходимые директории если они не существуют"""
    os.makedirs(IPv4_DIR, exist_ok=True)
    os.makedirs(IPv6_DIR, exist_ok=True)

def subnet_summarization(subnet_list):
    """Объединяет подсети в суммаризованные"""
    subnets = [ipaddress.ip_network(subnet) for subnet in subnet_list]
    return list(ipaddress.collapse_addresses(subnets))

def process_subnets(subnet_list, target_as):
    """Обрабатывает подсети для конкретного AS номера"""
    ipv4_subnets = []
    ipv6_subnets = []

    for subnet_str, as_number in subnet_list:
        try:
            subnet = ipaddress.ip_network(subnet_str)
            if as_number == target_as:
                if subnet.version == 4:
                    ipv4_subnets.append(subnet_str)
                elif subnet.version == 6:
                    ipv6_subnets.append(subnet_str)
        except ValueError:
            print(f"Invalid subnet: {subnet_str}")
            sys.exit(1)

    ipv4_merged = subnet_summarization(ipv4_subnets) if ipv4_subnets else []
    ipv6_merged = subnet_summarization(ipv6_subnets) if ipv6_subnets else []

    return ipv4_merged, ipv6_merged

def download_ready_subnets(url_v4, url_v6):
    """Загружает готовые подсети по URL"""
    ipv4_subnets = []
    ipv6_subnets = []

    urls = [(url_v4, 4), (url_v6, 6)]

    for url, version in urls:
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req) as response:
                if response.status == 200:
                    subnets = response.read().decode('utf-8').splitlines()
                    for subnet_str in subnets:
                        try:
                            subnet = ipaddress.ip_network(subnet_str)
                            if subnet.version == 4:
                                ipv4_subnets.append(subnet_str)
                            elif subnet.version == 6:
                                ipv6_subnets.append(subnet_str)
                        except ValueError:
                            print(f"Invalid subnet: {subnet_str}")
        except Exception as e:
            print(f"Query error: {e}")

    return ipv4_subnets, ipv6_subnets

def download_ready_split_subnets(url):
    """Загружает и разделяет подсети по версии IP"""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as response:
        subnets = response.read().decode('utf-8').splitlines()

    ipv4_subnets = []
    ipv6_subnets = []

    for cidr in subnets:
        try:
            subnet = ipaddress.ip_network(cidr.strip())
            if subnet.version == 4:
                ipv4_subnets.append(str(subnet))
            elif subnet.version == 6:
                ipv6_subnets.append(str(subnet))
        except ValueError:
            print(f"Invalid subnet: {cidr}")

    return ipv4_subnets, ipv6_subnets

def write_subnets_to_file(subnets, filename):
    """Записывает подсети в файл"""
    with open(filename, 'w') as file:
        for subnet in subnets:
            file.write(f'{subnet}\n')

def copy_file_legacy(src_filename):
    """Создает копию файла с другим именем"""
    base_filename = os.path.basename(src_filename)
    new_filename = base_filename.capitalize()
    shutil.copy(src_filename, os.path.join(os.path.dirname(src_filename), new_filename))

def main():
    # Создаем необходимые директории
    create_directories()

    # Загружаем данные BGP
    subnet_list = []
    request = urllib.request.Request(BGP_TOOLS_URL, headers=HEADERS)

    try:
        with urllib.request.urlopen(request) as response:
            for line in response:
                decoded_line = line.decode('utf-8').strip()
                parts = decoded_line.split()
                if len(parts) >= 2:
                    subnet, as_number = parts[0], parts[1]
                    subnet_list.append((subnet, as_number))
    except Exception as e:
        print(f"Error downloading BGP data: {e}")
        sys.exit(1)

    # Обрабатываем различные AS
    targets = [
        (AS_META, META),
        (AS_TWITTER, TWITTER),
        (AS_HETZNER, HETZNER),
        (AS_OVH, OVH)
    ]

    for as_number, filename in targets:
        ipv4_merged, ipv6_merged = process_subnets(subnet_list, as_number)
        write_subnets_to_file(ipv4_merged, f'{IPv4_DIR}/{filename}')
        write_subnets_to_file(ipv6_merged, f'{IPv6_DIR}/{filename}')
        copy_file_legacy(f'{IPv4_DIR}/{filename}')
        copy_file_legacy(f'{IPv6_DIR}/{filename}')

    # Обрабатываем Discord
    ipv4_discord, ipv6_discord = download_ready_subnets(DISCORD_VOICE_V4, DISCORD_VOICE_V6)
    write_subnets_to_file(ipv4_discord, f'{IPv4_DIR}/{DISCORD}')
    write_subnets_to_file(ipv6_discord, f'{IPv6_DIR}/{DISCORD}')
    copy_file_legacy(f'{IPv4_DIR}/{DISCORD}')
    copy_file_legacy(f'{IPv6_DIR}/{DISCORD}')

    # Обрабатываем Telegram
    ipv4_telegram, ipv6_telegram = download_ready_split_subnets(TELEGRAM_CIDR_URL)
    write_subnets_to_file(ipv4_telegram, f'{IPv4_DIR}/{TELEGRAM}')
    write_subnets_to_file(ipv6_telegram, f'{IPv6_DIR}/{TELEGRAM}')

    # Обрабатываем Cloudflare
    ipv4_cloudflare, ipv6_cloudflare = download_ready_subnets(CLOUDFLARE_V4, CLOUDFLARE_V6)
    write_subnets_to_file(ipv4_cloudflare, f'{IPv4_DIR}/{CLOUDFLARE}')
    write_subnets_to_file(ipv6_cloudflare, f'{IPv6_DIR}/{CLOUDFLARE}')

if __name__ == '__main__':
    main()

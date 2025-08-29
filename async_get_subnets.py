#!/usr/bin/python3

import ipaddress
import aiohttp
import asyncio
import yaml
import os
import shutil
import sys
from typing import List, Tuple, Dict, Any, Optional
from pathlib import Path

# Проверка версии Python
if sys.version_info < (3, 10):
    print("Требуется Python версии 3.10 или выше")
    sys.exit(1)


class SubnetProcessor:
    def __init__(self, config_path: str = "config.yaml"):
        self.config = self.load_config(config_path)
        self.session = None
        self.gateway = self.config.get('gateway', '192.168.1.1')

    def load_config(self, config_path: str) -> Dict[str, Any]:
        """Загружает конфигурацию из YAML файла"""
        try:
            with open(config_path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            print(f"Конфигурационный файл {config_path} не найден")
            sys.exit(1)
        except yaml.YAMLError as e:
            print(f"Ошибка в формате конфигурационного файла: {e}")
            sys.exit(1)

    def generate_names(self, name: str, entity_type: str, entity_key: str) -> Dict[str, str]:
        """Генерирует имена файлов, списков и комментарии на основе шаблонов"""
        templates = self.config.get('name_templates', {})
        custom_settings = self.config.get('custom_settings', {}).get(entity_key, {})

        name_upper = name.upper()
        name_title = name.capitalize()

        # Базовые значения из шаблонов
        result = {
            'file': templates.get('file', '{name}.lst').format(name=name, name_upper=name_upper, name_title=name_title),
            'list_name': templates.get('list_name', '{name_upper}').format(name=name, name_upper=name_upper,
                                                                           name_title=name_title),
            'comment': templates.get('comment', '{name_title} networks').format(name=name, name_upper=name_upper,
                                                                                name_title=name_title)
        }

        # Переопределяем кастомными настройками
        result.update(custom_settings)

        return result

    async def create_session(self):
        """Создает aiohttp сессию"""
        self.session = aiohttp.ClientSession(headers=self.config.get('headers', {}))

    async def close_session(self):
        """Закрывает aiohttp сессию"""
        if self.session:
            await self.session.close()

    async def download_url(self, url: str) -> str:
        """Асинхронно загружает данные по URL"""
        if not self.session:
            await self.create_session()

        try:
            async with self.session.get(url) as response:
                response.raise_for_status()
                return await response.text()
        except aiohttp.ClientError as e:
            print(f"Ошибка при загрузке {url}: {e}")
            return ""

    def create_directories(self):
        """Создает необходимые директории если они не существуют"""
        os.makedirs(self.config['ipv4_dir'], exist_ok=True)
        os.makedirs(self.config['routeros_dir'], exist_ok=True)

    def subnet_summarization(self, subnet_list: List[str]) -> List[ipaddress.IPv4Network]:
        """Объединяет подсети в суммаризованные"""
        subnets = [ipaddress.ip_network(subnet) for subnet in subnet_list]
        return list(ipaddress.collapse_addresses(subnets))

    async def download_bgp_table(self) -> List[Tuple[str, str]]:
        """Загружает и парсит таблицу BGP"""
        data = await self.download_url(self.config['bgp_tools_url'])
        if not data:
            return []

        subnet_list = []
        for line in data.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2:
                subnet, as_number = parts[0], parts[1]
                subnet_list.append((subnet, as_number))

        return subnet_list

    def process_subnets(self, subnet_list: List[Tuple[str, str]], target_as: str) -> List[ipaddress.IPv4Network]:
        """Обрабатывает подсети для конкретного AS номера"""
        ipv4_subnets = []

        for subnet_str, as_number in subnet_list:
            try:
                subnet = ipaddress.ip_network(subnet_str)
                if as_number == target_as and subnet.version == 4:
                    ipv4_subnets.append(subnet_str)
            except ValueError:
                print(f"Invalid subnet: {subnet_str}")
                continue

        return self.subnet_summarization(ipv4_subnets) if ipv4_subnets else []

    async def download_ready_subnets(self, url: str) -> List[ipaddress.IPv4Network]:
        """Загружает готовые подсети по URL"""
        data = await self.download_url(url)
        if not data:
            return []

        ipv4_subnets = []
        for subnet_str in data.splitlines():
            try:
                subnet = ipaddress.ip_network(subnet_str.strip())
                if subnet.version == 4:
                    ipv4_subnets.append(str(subnet))
            except ValueError:
                print(f"Invalid subnet: {subnet_str}")
                continue

        return self.subnet_summarization(ipv4_subnets) if ipv4_subnets else []

    def write_subnets_to_file(self, subnets: List[ipaddress.IPv4Network], filename: str):
        """Записывает подсети в файл"""
        with open(filename, 'w') as file:
            for subnet in subnets:
                file.write(f'{subnet}\n')

    def copy_file_legacy(self, src_filename: str):
        """Создает копию файла с другим именем"""
        base_filename = os.path.basename(src_filename)
        new_filename = base_filename.capitalize()
        shutil.copy(src_filename, os.path.join(os.path.dirname(src_filename), new_filename))

    def generate_routeros_config(self, subnets: List[ipaddress.IPv4Network], list_name: str, comment: str):
        """Генерирует конфигурацию для RouterOS"""
        config_path = os.path.join(self.config['routeros_dir'], f"{list_name}.rsc")

        with open(config_path, 'w') as f:
            # Добавляем адреса в address-list
            for subnet in subnets:
                f.write(f"/ip firewall address-list add address={subnet} list={list_name} comment=\"{comment}\"\n")

            # Добавляем правила mangle и route
            f.write(f'''
{{
:local rrule [/ip firewall mangle find dst-address-list ="R-{list_name}" ]
:local rroute [/ip route find routing-table="R-{list_name}"]
:if ([:len [:tostr $rrule]] = 0 ) do {{ 
        :do {{
                /ip firewall mangle add action=mark-routing chain=prerouting connection-mark=no-mark dst-address-list={list_name} new-routing-mark=R-{list_name} passthrough=no 
            }} on-error={{}};
        }} 
:if ([:len [:tostr $rroute]] = 0) do={{
    :do {{/ip route add comment= distance=1 gateway={self.gateway} routing-mark=R-{list_name}}} on-error={{}}
}}
''')

    async def process_as_numbers(self, subnet_list: List[Tuple[str, str]]):
        """Обрабатывает AS номера из конфигурации"""
        for as_number, as_config in self.config.get('as_numbers', {}).items():
            subnets = self.process_subnets(subnet_list, as_number)

            if subnets:
                # Генерируем имена на основе шаблонов
                names = self.generate_names(as_config['name'], 'as_number', as_number)

                filename = os.path.join(self.config['ipv4_dir'], names['file'])
                self.write_subnets_to_file(subnets, filename)
                self.copy_file_legacy(filename)

                # Генерируем конфигурацию RouterOS
                self.generate_routeros_config(subnets, names['list_name'], names['comment'])

    async def process_services(self):
        """Обрабатывает сервисы из конфигурации"""
        services = self.config.get('services', {})

        for service_key, service_config in services.items():
            subnets = await self.download_ready_subnets(service_config['url'])

            if subnets:
                # Генерируем имена на основе шаблонов
                names = self.generate_names(service_config['name'], 'service', service_key)

                filename = os.path.join(self.config['ipv4_dir'], names['file'])
                self.write_subnets_to_file(subnets, filename)

                # Для discord создаем legacy копию
                if service_key == 'discord':
                    self.copy_file_legacy(filename)

                # Генерируем конфигурацию RouterOS
                self.generate_routeros_config(subnets, names['list_name'], names['comment'])

    async def run(self):
        """Основной метод выполнения"""
        self.create_directories()

        # Загружаем данные BGP
        subnet_list = await self.download_bgp_table()
        if not subnet_list:
            print("Не удалось загрузить BGP таблицу")
            return

        # Обрабатываем все источники
        await self.process_as_numbers(subnet_list)
        await self.process_services()

        await self.close_session()


async def main():
    processor = SubnetProcessor()
    await processor.run()


if __name__ == '__main__':
    asyncio.run(main())
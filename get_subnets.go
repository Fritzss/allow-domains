package main

import (
        "bufio"
        "fmt"
        "io"
        "log"
        "net/http"
        "net/netip"
        "os"
        "path/filepath"
        "strings"

        "gopkg.in/yaml.v3"
        "go4.org/netipx"
)

// Config структура для конфигурации YAML
type Config struct {
        BGPToolsURL    string              `yaml:"bgp_tools_url"`
        UserAgent      string              `yaml:"user_agent"`
        IPv4Dir        string              `yaml:"ipv4_dir"`
        RouterOSDir    string              `yaml:"routeros_dir"`
        ASNumbers      map[string]ASConfig `yaml:"as_numbers"`
        Discord        DiscordConfig       `yaml:"discord"`
        Telegram       TelegramConfig      `yaml:"telegram"`
        Cloudflare     CloudflareConfig    `yaml:"cloudflare"`
        AdditionalAS   map[string]ASConfig `yaml:"additional_as"`
        GenerateV6     bool                `yaml:"generate_v6"`
        GenerateV7     bool                `yaml:"generate_v7"`
        Gateway        string              `yaml:"gateway"` // Единый шлюз для всех маршрутов
}

type ASConfig struct {
        File     string `yaml:"file"`
        ListName string `yaml:"list_name"`
        Comment  string `yaml:"comment"`
}

type DiscordConfig struct {
        VoiceV4  string `yaml:"voice_v4"`
        File     string `yaml:"file"`
        ListName string `yaml:"list_name"`
}

type TelegramConfig struct {
        CIDRURL  string `yaml:"cidr_url"`
        File     string `yaml:"file"`
        ListName string `yaml:"list_name"`
}

type CloudflareConfig struct {
        V4       string `yaml:"v4"`
        File     string `yaml:"file"`
        ListName string `yaml:"list_name"`
}

type subnetAS struct {
        subnet string
        as     string
}

var config Config

func loadConfig(configPath string) error {
        data, err := os.ReadFile(configPath)
        if err != nil {
                return err
        }

        err = yaml.Unmarshal(data, &config)
        if err != nil {
                return err
        }

        // Set defaults if not specified
        if config.RouterOSDir == "" {
                config.RouterOSDir = "RouterOS"
        }

        // By default, generate both v6 and v7 configs
        if !config.GenerateV6 && !config.GenerateV7 {
                config.GenerateV6 = true
                config.GenerateV7 = true
        }

        return nil
}

func createDirs() error {
        if err := os.MkdirAll(config.IPv4Dir, 0755); err != nil {
                return err
        }

        // Create version-specific directories if needed
        if config.GenerateV6 {
                if err := os.MkdirAll(filepath.Join(config.RouterOSDir, "v6"), 0755); err != nil {
                        return err
                }
        }
        if config.GenerateV7 {
                if err := os.MkdirAll(filepath.Join(config.RouterOSDir, "v7"), 0755); err != nil {
                        return err
                }
        }

        return nil
}

func downloadURL(url string) (string, error) {
        client := &http.Client{}
        req, err := http.NewRequest("GET", url, nil)
        if err != nil {
                return "", err
        }
        req.Header.Set("User-Agent", config.UserAgent)

        resp, err := client.Do(req)
        if err != nil {
                return "", err
        }
        defer resp.Body.Close()

        if resp.StatusCode != http.StatusOK {
                return "", fmt.Errorf("HTTP error: %s", resp.Status)
        }

        body, err := io.ReadAll(resp.Body)
        if err != nil {
                return "", err
        }

        return string(body), nil
}

func downloadBGPTable() ([]subnetAS, error) {
        data, err := downloadURL(config.BGPToolsURL)
        if err != nil {
                return nil, err
        }

        var subnets []subnetAS
        scanner := bufio.NewScanner(strings.NewReader(data))
        for scanner.Scan() {
                line := strings.TrimSpace(scanner.Text())
                parts := strings.Fields(line)
                if len(parts) >= 2 {
                        subnets = append(subnets, subnetAS{
                                subnet: parts[0],
                                as:     parts[1],
                        })
                }
        }

        if err := scanner.Err(); err != nil {
                return nil, err
        }

        return subnets, nil
}

func processSubnets(subnets []subnetAS, targetAS string) ([]netip.Prefix, error) {
        var v4Set netipx.IPSetBuilder

        for _, item := range subnets {
                if item.as == targetAS {
                        prefix, err := netip.ParsePrefix(item.subnet)
                        if err != nil {
                                log.Printf("Invalid subnet: %s", item.subnet)
                                continue
                        }

                        if prefix.Addr().Is4() {
                                v4Set.AddPrefix(prefix)
                        }
                }
        }

        v4IPSet, _ := v4Set.IPSet()
        return v4IPSet.Prefixes(), nil
}

func downloadReadySubnets(urlV4 string) ([]netip.Prefix, error) {
        var v4Set netipx.IPSetBuilder

        data, err := downloadURL(urlV4)
        if err != nil {
                return nil, err
        }

        scanner := bufio.NewScanner(strings.NewReader(data))
        for scanner.Scan() {
                line := strings.TrimSpace(scanner.Text())
                if line == "" {
                        continue
                }

                prefix, err := netip.ParsePrefix(line)
                if err != nil {
                        log.Printf("Invalid subnet: %s", line)
                        continue
                }

                if prefix.Addr().Is4() {
                        v4Set.AddPrefix(prefix)
                }
        }

        v4IPSet, _ := v4Set.IPSet()
        return v4IPSet.Prefixes(), nil
}

func downloadReadySplitSubnets(url string) ([]netip.Prefix, error) {
        data, err := downloadURL(url)
        if err != nil {
                return nil, err
        }

        var v4Set netipx.IPSetBuilder
        scanner := bufio.NewScanner(strings.NewReader(data))
        for scanner.Scan() {
                line := strings.TrimSpace(scanner.Text())
                if line == "" {
                        continue
                }

                prefix, err := netip.ParsePrefix(line)
                if err != nil {
                        log.Printf("Invalid subnet: %s", line)
                        continue
                }

                if prefix.Addr().Is4() {
                        v4Set.AddPrefix(prefix)
                }
        }

        if err := scanner.Err(); err != nil {
                return nil, err
        }

        v4IPSet, _ := v4Set.IPSet()
        return v4IPSet.Prefixes(), nil
}

func writeSubnetsToFile(prefixes []netip.Prefix, filename string) error {
        file, err := os.Create(filename)
        if err != nil {
                return err
        }
        defer file.Close()

        writer := bufio.NewWriter(file)
        for _, prefix := range prefixes {
                _, err := writer.WriteString(prefix.String() + "\n")
                if err != nil {
                        return err
                }
        }
        return writer.Flush()
}

func copyFileLegacy(srcFilename string) error {
        base := filepath.Base(srcFilename)
        destFilename := filepath.Join(filepath.Dir(srcFilename), strings.Title(base))

        srcFile, err := os.Open(srcFilename)
        if err != nil {
                return err
        }
        defer srcFile.Close()

        destFile, err := os.Create(destFilename)
        if err != nil {
                return err
        }
        defer destFile.Close()

        _, err = io.Copy(destFile, srcFile)
        return err
}

func generateRouterOSVersionedConfig(listName, comment string, prefixes []netip.Prefix, outputDir, version string) error {
        // Создаем директорию, если не существует
        if err := os.MkdirAll(outputDir, 0755); err != nil {
                return err
        }

        // Формируем имя файла
        filename := filepath.Join(outputDir, listName+".rsc")

        file, err := os.Create(filename)
        if err != nil {
                return err
        }
        defer file.Close()

        writer := bufio.NewWriter(file)

        // Определяем путь в зависимости от версии RouterOS
        var path string
        if version == "v6" {
                path = "/ip firewall address-list"
        } else { // v7
                path = "/ip/firewall/address-list"
        }

        // Записываем команды для каждой подсети
        for _, prefix := range prefixes {
                cmd := fmt.Sprintf("do {%s add address=%s comment=%s list=%s } on-error={}\n",
                        path, prefix.String(), comment, listName)
                _, err := writer.WriteString(cmd)
                if err != nil {
                        return err
                }
        }

        // Добавляем правила mangle и route
        manglePath := "/ip firewall mangle"
        routePath := "/ip route"
        if version == "v7" {
                manglePath = "/ip/firewall/mangle"
                routePath = "/ip/route"
        }

        script := fmt.Sprintf(`
{
   :local rrule [ %[1]s find dst-address-list="%[2]s" ]
   :if ([:len $rrule ] = 0 ) do={
          :do {
           %[1]s add action=mark-routing chain=prerouting connection-mark=no-mark dst-address-list=%[2]s new-routing-mark="R_%[2]s"                                                            passthrough=no
            } on-error={};
   :local rroute [%[3]s find routing-table="R_%[2]s" gateway=%[4]s ]
   :if ([:len $rroute ] = 0) do={
    do {%[3]s add comment=%[2]s distance=1 gateway=%[4]s routing-mark="R_%[2]s"} on-error={}
 }
`, manglePath,
   listName,
   routePath,
   config.Gateway,)

        _, err = writer.WriteString(script)
        if err != nil {
                return err
        }
        return writer.Flush()
}

func generateRouterOSConfig(listName, comment string, v4Prefixes []netip.Prefix, outputDir string) error {
        // Генерируем конфиги для разных версий RouterOS
        if config.GenerateV6 {
                v6Dir := filepath.Join(outputDir, "v6")
                if len(v4Prefixes) > 0 {
                        if err := generateRouterOSVersionedConfig(listName, comment, v4Prefixes, v6Dir, "v6"); err != nil {
                                return err
                        }
                }
        }

        if config.GenerateV7 {
                v7Dir := filepath.Join(outputDir, "v7")
                if len(v4Prefixes) > 0 {
                        if err := generateRouterOSVersionedConfig(listName, comment, v4Prefixes, v7Dir, "v7"); err != nil {
                                return err
                        }
                }
        }

        return nil
}

func main() {
        // Загрузка конфигурации
        if len(os.Args) < 2 {
                log.Fatal("Usage: get_subnets <config-file>")
        }

        if err := loadConfig(os.Args[1]); err != nil {
                log.Fatal("Error loading config:", err)
        }

        if err := createDirs(); err != nil {
                log.Fatal(err)
        }

        // Download BGP table
        subnets, err := downloadBGPTable()
        if err != nil {
                log.Fatal("Error downloading BGP table:", err)
        }

        // Process predefined AS numbers
        for as, asConfig := range config.ASNumbers {
                v4Merged, err := processSubnets(subnets, as)
                if err != nil {
                        log.Printf("Error processing subnets for AS %s: %v", as, err)
                        continue
                }

                listName := asConfig.ListName
                if listName == "" {
                        listName = strings.TrimSuffix(asConfig.File, ".lst")
                }
                comment := asConfig.Comment
                if comment == "" {
                        comment = as
                }

                // Записываем подсети в файлы
                if err := writeSubnetsToFile(v4Merged, filepath.Join(config.IPv4Dir, asConfig.File)); err != nil {
                        log.Printf("Error writing %s IPv4: %v", asConfig.File, err)
                }

                // Создаем файлы const.rsc для MikroTik
                if err := generateRouterOSConfig(listName, comment, v4Merged, config.RouterOSDir); err != nil {
                        log.Printf("Error generating RouterOS config for %s: %v", listName, err)
                }

                if err := copyFileLegacy(filepath.Join(config.IPv4Dir, asConfig.File)); err != nil {
                        log.Printf("Error creating legacy copy for %s IPv4: %v", asConfig.File, err)
                }
        }

        // Process Discord
        v4Discord, err := downloadReadySubnets(config.Discord.VoiceV4)
        if err != nil {
                log.Printf("Error downloading Discord subnets: %v", err)
        } else {
                filename := config.Discord.File
                if filename == "" {
                        filename = "discord.lst"
                }
                listName := config.Discord.ListName
                if listName == "" {
                        listName = strings.TrimSuffix(filename, ".lst")
                }

                if err := writeSubnetsToFile(v4Discord, filepath.Join(config.IPv4Dir, filename)); err != nil {
                        log.Printf("Error writing Discord IPv4: %v", err)
                }

                // Создаем файлы const.rsc для Discord
                if err := generateRouterOSConfig(listName, "DISCORD", v4Discord, config.RouterOSDir); err != nil {
                        log.Printf("Error generating RouterOS config for Discord: %v", err)
                }

                if err := copyFileLegacy(filepath.Join(config.IPv4Dir, filename)); err != nil {
                        log.Printf("Error creating legacy copy for Discord IPv4: %v", err)
                }
        }

        // Process Telegram
        v4Telegram, err := downloadReadySplitSubnets(config.Telegram.CIDRURL)
        if err != nil {
                log.Printf("Error downloading Telegram subnets: %v", err)
        } else {
                filename := config.Telegram.File
                if filename == "" {
                        filename = "telegram.lst"
                }
                listName := config.Telegram.ListName
                if listName == "" {
                        listName = strings.TrimSuffix(filename, ".lst")
                }

                if err := writeSubnetsToFile(v4Telegram, filepath.Join(config.IPv4Dir, filename)); err != nil {
                        log.Printf("Error writing Telegram IPv4: %v", err)
                }

                // Создаем файлы const.rsc для Telegram
                if err := generateRouterOSConfig(listName, "TELEGRAM", v4Telegram, config.RouterOSDir); err != nil {
                        log.Printf("Error generating RouterOS config for Telegram: %v", err)
                }
        }

        // Process Cloudflare
        v4Cloudflare, err := downloadReadySubnets(config.Cloudflare.V4)
        if err != nil {
                log.Printf("Error downloading Cloudflare subnets: %v", err)
        } else {
                filename := config.Cloudflare.File
                if filename == "" {
                        filename = "cloudflare.lst"
                }
                listName := config.Cloudflare.ListName
                if listName == "" {
                        listName = strings.TrimSuffix(filename, ".lst")
                }

                if err := writeSubnetsToFile(v4Cloudflare, filepath.Join(config.IPv4Dir, filename)); err != nil {
                        log.Printf("Error writing Cloudflare IPv4: %v", err)
                }

                // Создаем файлы const.rsc для Cloudflare
                if err := generateRouterOSConfig(listName, "CLOUDFLARE", v4Cloudflare, config.RouterOSDir); err != nil {
                        log.Printf("Error generating RouterOS config for Cloudflare: %v", err)
                }
        }

        log.Println("Done!")
}

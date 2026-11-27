/***************************************************************************
*                           STAR WARS REALITY 1.0                          *
*--------------------------------------------------------------------------*
* Star Wars Reality Code Additions and changes from the Smaug Code         *
* copyright (c) 1997 by Sean Cooper                                        *
* -------------------------------------------------------------------------*
* Starwars and Starwars Names copyright(c) Lucas Film Ltd.                 *
*--------------------------------------------------------------------------*
* SMAUG 1.0 (C) 1994, 1995, 1996 by Derek Snider                           *
* SMAUG code team: Thoric, Altrag, Blodkai, Narn, Haus,                    *
* Scryn, Rennard, Swordbearer, Gorog, Grishnakh and Tricops                *
* ------------------------------------------------------------------------ *
* Merc 2.1 Diku Mud improvments copyright (C) 1992, 1993 by Michael        *
* Chastain, Michael Quan, and Mitchell Tse.                                *
* Original Diku Mud copyright (C) 1990, 1991 by Sebastian Hammer,          *
* Michael Seifert, Hans Henrik St{rfeldt, Tom Madsen, and Katja Nyboe.     *
* ------------------------------------------------------------------------ *
*			 GMCP Handling			                                       *
****************************************************************************/

#include <sys/types.h>
#include <sys/time.h>
#include <sys/stat.h>
#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <time.h>
#include <string.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <sys/types.h>
#include <unistd.h>
#include <climits>
#include <vector>

#include "mud.h"

/*
Additional things to add:
Character class/ability
Character configs
Character position/status
Character drug/addictions
Character languages
Character affect data

Combat data:
Target data (name, status)

Ships:
Ship status
Target status
System data
Hyperspace jump data

Character Skills
Character Items
Room data
People in room
objects in room
Ships in room

*/

void handle_gmcp(DESCRIPTOR_DATA *d, unsigned char *data, int len);
void send_gmcp(DESCRIPTOR_DATA *d, const std::string &msg);
void dispatch_gmcp(DESCRIPTOR_DATA *d, const std::string &module, const std::string &data);
void gmcp_core_ping(DESCRIPTOR_DATA *d, const std::string &data);
void gmcp_core_hello(DESCRIPTOR_DATA *d, const std::string &data);
void gmcp_sendf(DESCRIPTOR_DATA *d, const std::string &module, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
void gmcp_send_char_vitals(DESCRIPTOR_DATA *d);
void gmcp_send_char_status(DESCRIPTOR_DATA *d);
void gmcp_send_room_info(DESCRIPTOR_DATA *d);
void gmcp_send_all(void (*func)(DESCRIPTOR_DATA *));
void gmcp_core_supports_set(DESCRIPTOR_DATA *d, const std::string &data);
void gmcp_core_supports_remove(DESCRIPTOR_DATA *d, const std::string &data);
void gmcp_core_supports_add(DESCRIPTOR_DATA *d, const std::string &data);
bool gmcp_is_subscribed(DESCRIPTOR_DATA *d, const std::string &module);
const char *gmcp_cache_get(DESCRIPTOR_DATA *d, const char *key);
const char *gmcp_cache_get(DESCRIPTOR_DATA *d, const std::string &key);

bool gmcp_cache_set(DESCRIPTOR_DATA *d, const char *key, const char *value);
bool gmcp_cache_set(DESCRIPTOR_DATA *d, const std::string &key, const std::string &value);
bool gmcp_cache_set(DESCRIPTOR_DATA *d, const std::string &key, const char *value);
bool gmcp_cache_set(DESCRIPTOR_DATA *d, const char *key, const std::string &value);

/* GMCP pending flags */
#define GMCP_PEND_VITALS    BV00
#define GMCP_PEND_ROOM      BV01
#define GMCP_PEND_STATUS    BV02
#define GMCP_PEND_STATS     BV03


/* === GMCP MODULE SYSTEM (NEW) =============================== */

typedef void (*gmcp_handler_f)(DESCRIPTOR_DATA *d, const std::string &data);

typedef struct
{
    const char *name;
    gmcp_handler_f handler;
} gmcp_module_t;

/* Forward declarations */
void gmcp_core_ping(DESCRIPTOR_DATA *d, const std::string &data);
void gmcp_core_hello(DESCRIPTOR_DATA *d, const std::string &data);
void dispatch_gmcp(DESCRIPTOR_DATA *d, const std::string &module, const std::string &data);

/* Module table */
static const gmcp_module_t gmcp_modules[] =
{
    { "Core.Ping",  gmcp_core_ping  },
    { "Core.Hello", gmcp_core_hello },

    /* future modules go here */
    { "Core.Supports.Set",  gmcp_core_supports_set },
    { "Core.Supports.Add",    gmcp_core_supports_add },
    { "Core.Supports.Remove", gmcp_core_supports_remove },    
    { NULL, NULL }
};
/* === GMCP JSON BUILDER ============================ */

typedef struct
{
    std::string buf;
    bool first;
} gmcp_json_t;

void gmcp_json_init(gmcp_json_t *j)
{
    j->buf = "{";
    j->first = true;
}

// Return the width of a complete scalar, or zero for an invalid byte.
static size_t gmcp_utf8_width(const char *s, size_t remaining)
{
    const unsigned char lead = s[0];
    if (lead < 0x80)
        return 1;
    if (lead < 0xc2 || lead > 0xf4)
        return 0;
    const size_t width = lead < 0xe0 ? 2 : lead < 0xf0 ? 3 : 4;
    if (width > remaining)
        return 0;

    const unsigned char second = s[1];
    if ((lead == 0xe0 && second < 0xa0) || (lead == 0xed && second >= 0xa0)
        || (lead == 0xf0 && second < 0x90) || (lead == 0xf4 && second >= 0x90))
        return 0;
    for (size_t i = 1; i < width; ++i)
        if ((static_cast<unsigned char>(s[i]) & 0xc0) != 0x80)
            return 0;
    return width;
}

static std::string gmcp_escape_json(const std::string &s)
{
    static constexpr char hex[] = "0123456789abcdef";
    std::string out;
    out.reserve(s.size());

    for (size_t i = 0; i < s.size();)
    {
        const unsigned char c = s[i];
        if (c < 0x20)
        {
            out += "\\u00";
            out += hex[c >> 4];
            out += hex[c & 0x0f];
            ++i;
        }
        else if (c == '\\' || c == '"')
        {
            out += '\\';
            out += c;
            ++i;
        }
        else
        {
            const size_t width = gmcp_utf8_width(s.data() + i, s.size() - i);
            if (width == 0)
            {
                out += "\\ufffd";
                ++i;
            }
            else
            {
                out.append(s, i, width);
                i += width;
            }
        }
    }
    return out;
}

void gmcp_json_add_int(gmcp_json_t *j, const std::string &key, int value)
{
    if (!j->first)
        j->buf += ",";
    j->first = false;
    j->buf += "\"" + gmcp_escape_json(key) + "\":" + std::to_string(value);
}

void gmcp_json_add_str(gmcp_json_t *j, const std::string &key, const std::string &val)
{
    if (!j->first)
        j->buf += ",";
    j->first = false;

    j->buf += "\"";
    j->buf += gmcp_escape_json(key);
    j->buf += "\":\"";
    j->buf += gmcp_escape_json(val);
    j->buf += "\"";
}

const char *gmcp_cache_get(DESCRIPTOR_DATA *d, const char *key)
{
    if (!d || !key)
        return NULL;

    for (int i = 0; i < d->gmcp_cache.count; i++)
    {
        if (!strcmp(d->gmcp_cache.entries[i].key, key))
            return d->gmcp_cache.entries[i].value;
    }
    return NULL;
}

const char *gmcp_cache_get(DESCRIPTOR_DATA *d, const std::string &key)
{
    return gmcp_cache_get(d, key.c_str());
}

const std::string &gmcp_json_close(gmcp_json_t *j)
{
    if (j->buf.empty() || j->buf.back() != '}')
        j->buf += "}";
    return j->buf;
}

void gmcp_json_add_cache_int(gmcp_json_t *j, DESCRIPTOR_DATA *d,
                            const char *json_key, const char *cache_key)
{
    const char *val = gmcp_cache_get(d, cache_key);
    if (val)
        gmcp_json_add_int(j, json_key, atoi(val));
}

void gmcp_json_add_cache_str(gmcp_json_t *j, DESCRIPTOR_DATA *d,
                            const char *json_key, const char *cache_key)
{
    const char *val = gmcp_cache_get(d, cache_key);
    if (val)
        gmcp_json_add_str(j, json_key, val);
}

static bool gmcp_desc_alive(const DESCRIPTOR_DATA *d)
{
    return comm_desc_alive(d);
}

void build_char_vitals(gmcp_json_t *j, DESCRIPTOR_DATA *d)
{
    if (!j || !d)
        return;

    gmcp_json_add_cache_int(j, d, "hp", "Char.Vitals.hp");
    gmcp_json_add_cache_int(j, d, "maxhp", "Char.Vitals.maxhp");
    if ( d->character && (d->character->skill_level[FORCE_ABILITY] > 1 || IS_IMMORTAL(d->character)) )
    {
        gmcp_json_add_cache_int(j, d, "force", "Char.Vitals.mana");
        gmcp_json_add_cache_int(j, d, "maxforce", "Char.Vitals.maxmana");
    }
    gmcp_json_add_cache_int(j, d, "move", "Char.Vitals.move");
    gmcp_json_add_cache_int(j, d, "maxmove", "Char.Vitals.maxmove");
    gmcp_json_add_cache_int(j, d, "credits", "Char.Vitals.credits");
    gmcp_json_add_cache_int(j, d, "bank", "Char.Vitals.bank");
    gmcp_json_add_cache_int(j, d, "pkills", "Char.Vitals.pkills");
    gmcp_json_add_cache_int(j, d, "mkills", "Char.Vitals.mkills");
    
   
}

void build_room_info(gmcp_json_t *j, DESCRIPTOR_DATA *d)
{
    if (!j || !d)
        return;

    gmcp_json_add_cache_int(j, d, "num",  "Room.Info.num");
    gmcp_json_add_cache_str(j, d, "name", "Room.Info.name");
    gmcp_json_add_cache_int(j, d, "rentship",  "Room.Info.rentship");
    gmcp_json_add_cache_int(j, d, "serinstop",  "Room.Info.serinstop");


}

void build_char_status(gmcp_json_t *j, DESCRIPTOR_DATA *d)
{
    if (!j || !d)
        return;

    gmcp_json_add_cache_str(j, d, "name", "Char.Status.name");
    gmcp_json_add_cache_str(j, d, "fullname", "Char.Status.fullname");
    gmcp_json_add_cache_str(j, d, "class", "Char.Status.class");
    gmcp_json_add_cache_str(j, d, "race", "Char.Status.race");    
    gmcp_json_add_cache_int(j, d, "wimpy",      "Char.Status.wimpy");
    gmcp_json_add_cache_str(j, d, "wanted",      "Char.Status.wanted");

    gmcp_json_add_cache_str(j, d, "clan", "Char.Status.clan");    
    gmcp_json_add_cache_int(j, d, "clan_salary", "Char.Status.salary");
    gmcp_json_add_cache_int(j, d, "clan_kills", "Char.Status.pkills");
    gmcp_json_add_cache_int(j, d, "clan_deaths", "Char.Status.pdeaths");

    if (d->character && !IS_NPC(d->character) && d->character->pcdata && IS_IMMORTAL(d->character))
    {
        gmcp_json_add_cache_int(j, d, "trust", "Char.Status.trust"); 
        gmcp_json_add_cache_int(j, d, "wizinvis", "Char.Status.wizinvis");    
        gmcp_json_add_cache_str(j, d, "bamfin", "Char.Status.Bamfin");    
        gmcp_json_add_cache_str(j, d, "bamfout", "Char.Status.Bamfout");    
        if (d->character->pcdata->area)
        {
            gmcp_json_add_cache_str(j, d, "arealoaded", "Char.Status.AreaLoaded");    
            gmcp_json_add_cache_int(j, d, "lrvnum", "Char.Status.LRVnum");    
            gmcp_json_add_cache_int(j, d, "hrvnum", "Char.Status.HRVnum");    
            gmcp_json_add_cache_int(j, d, "lovnum", "Char.Status.LOVnum");    
            gmcp_json_add_cache_int(j, d, "hovnum", "Char.Status.HOVnum");    
            gmcp_json_add_cache_int(j, d, "lmvnum", "Char.Status.LMVnum");    
            gmcp_json_add_cache_int(j, d, "hmvnum", "Char.Status.HMVnum");    

        }
    }    

}

void build_char_stats(gmcp_json_t *j, DESCRIPTOR_DATA *d)
{
    if (!j || !d)
        return;

    gmcp_json_add_cache_int(j, d, "str",        "Char.Stats.str");
    gmcp_json_add_cache_int(j, d, "dex",        "Char.Stats.dex");
    gmcp_json_add_cache_int(j, d, "con",        "Char.Stats.con");
    gmcp_json_add_cache_int(j, d, "int",        "Char.Stats.int");    
    gmcp_json_add_cache_int(j, d, "wis",        "Char.Stats.wis");    
    gmcp_json_add_cache_int(j, d, "cha",        "Char.Stats.cha");    
    gmcp_json_add_cache_int(j, d, "hitroll",    "Char.Stats.hitroll");    
    gmcp_json_add_cache_int(j, d, "damroll",    "Char.Stats.damroll");    
    gmcp_json_add_cache_int(j, d, "armor",      "Char.Stats.armor");    
    gmcp_json_add_cache_int(j, d, "align",      "Char.Stats.align");    
    gmcp_json_add_cache_int(j, d, "weight",      "Char.Stats.weight");    
    gmcp_json_add_cache_int(j, d, "maxweight",      "Char.Stats.maxweight");    
    gmcp_json_add_cache_int(j, d, "numitems",      "Char.Stats.numitems");    
    gmcp_json_add_cache_int(j, d, "maxitems",      "Char.Stats.maxnumitems");


}

void gmcp_send_from_cache(DESCRIPTOR_DATA *d, const char *module,
                          void (*builder)(gmcp_json_t *, DESCRIPTOR_DATA *))
{
    if (!d || !module || !builder || d->closing || d->descriptor < 0)
        return;

    gmcp_json_t j;
    gmcp_json_init(&j);

    builder(&j, d);

    if (!j.first)
        gmcp_sendf(d, module, "%s", gmcp_json_close(&j).c_str());
}

int gmcp_cache_get_int(DESCRIPTOR_DATA *d, const char *key)
{
    const char *v = gmcp_cache_get(d, key);
    return v ? atoi(v) : 0;
}

bool gmcp_cache_set(DESCRIPTOR_DATA *d, const char *key, const char *value)
{
    if (!d || !key || !value || strlen(key) >= sizeof(d->gmcp_cache.entries[0].key))
        return false;

    const size_t capacity = sizeof(d->gmcp_cache.entries[0].value) - 1;
    const size_t length = strlen(value);
    size_t prefix = 0;
    while (prefix < length && prefix < capacity)
    {
        const size_t width = gmcp_utf8_width(value + prefix, length - prefix);
        if (width > capacity - prefix)
            break;
        prefix += width ? width : 1;
    }

    for (int i = 0; i < d->gmcp_cache.count; ++i)
    {
        auto &entry = d->gmcp_cache.entries[i];
        if (strcmp(entry.key, key) != 0)
            continue;
        if (strlen(entry.value) == prefix && memcmp(entry.value, value, prefix) == 0)
            return false;

        memcpy(entry.value, value, prefix);
        entry.value[prefix] = '\0';
        return true;
    }

    if (d->gmcp_cache.count >= GMCP_MAX_CACHE)
        return false;

    auto &entry = d->gmcp_cache.entries[d->gmcp_cache.count++];
    strcpy(entry.key, key);
    memcpy(entry.value, value, prefix);
    entry.value[prefix] = '\0';
    return true;
}

bool gmcp_cache_set(DESCRIPTOR_DATA *d, const std::string &key, const std::string &value)
{
    return gmcp_cache_set(d, key.c_str(), value.c_str());
}

bool gmcp_cache_set(DESCRIPTOR_DATA *d, const std::string &key, const char *value)
{
    return gmcp_cache_set(d, key.c_str(), value);
}

bool gmcp_cache_set(DESCRIPTOR_DATA *d, const char *key, const std::string &value)
{
    return gmcp_cache_set(d, key, value.c_str());
}

bool gmcp_cache_set_int(DESCRIPTOR_DATA *d, const char *key, int value)
{
    return gmcp_cache_set(d, key, std::to_string(value));
}

void gmcp_cache_clear_prefix(DESCRIPTOR_DATA *d, const char *prefix)
{
    if (!d || !prefix)
        return;

    for (int i = 0; i < d->gmcp_cache.count; )
    {
        if (!strncmp(d->gmcp_cache.entries[i].key, prefix, strlen(prefix)))
        {
            d->gmcp_cache.entries[i] =
                d->gmcp_cache.entries[d->gmcp_cache.count - 1];
            d->gmcp_cache.count--;
        }
        else
        {
            i++;
        }
    }
}

void handle_gmcp(DESCRIPTOR_DATA *d, unsigned char *data, int len)
{
    if (!d || !data || len <= 0)
        return;

    std::string msg(reinterpret_cast<const char *>(data), len);

    const size_t pos = msg.find(' ');

    if (pos != std::string::npos)
        dispatch_gmcp(d, msg.substr(0, pos), msg.substr(pos + 1));
    else
        dispatch_gmcp(d, msg, "");
}

void dispatch_gmcp(DESCRIPTOR_DATA *d, const std::string &module, const std::string &data)
{
    if (!d)
        return;

    for (int i = 0; gmcp_modules[i].name != NULL; i++)
    {
        if (module == gmcp_modules[i].name)
        {
            gmcp_modules[i].handler(d, data);
            return;
        }
    }

}

static constexpr char gmcp_whitespace[] = " \t\r\n";

static std::string gmcp_trim_token(const std::string &s)
{
    const size_t start = s.find_first_not_of(gmcp_whitespace);
    if (start == std::string::npos)
        return "";
    return s.substr(start, s.find_last_not_of(gmcp_whitespace) - start + 1);
}

static size_t gmcp_skip_whitespace(const std::string &data, size_t pos)
{
    const size_t next = data.find_first_not_of(gmcp_whitespace, pos);
    return next == std::string::npos ? data.size() : next;
}

static int gmcp_read_hex_quad(const std::string &data, size_t &pos)
{
    if (data.size() - pos < 4)
        return -1;

    int value = 0;
    for (int i = 0; i < 4; ++i)
    {
        const unsigned char c = data[pos++];
        value *= 16;
        if (c >= '0' && c <= '9')
            value += c - '0';
        else if (c >= 'a' && c <= 'f')
            value += c - 'a' + 10;
        else if (c >= 'A' && c <= 'F')
            value += c - 'A' + 10;
        else
            return -1;
    }
    return value;
}

static bool gmcp_read_json_string(const std::string &data, size_t &pos, std::string &out)
{
    if (pos >= data.size() || data[pos++] != '"')
        return false;

    out.clear();
    while (pos < data.size())
    {
        const unsigned char c = data[pos++];
        if (c == '"')
            return true;
        if (c < 0x20)
            return false;
        if (c != '\\')
        {
            out += c;
            continue;
        }
        if (pos == data.size())
            return false;

        switch (data[pos++])
        {
            case '"': out += '"'; break;
            case '\\': out += '\\'; break;
            case '/': out += '/'; break;
            case 'b': out += '\b'; break;
            case 'f': out += '\f'; break;
            case 'n': out += '\n'; break;
            case 'r': out += '\r'; break;
            case 't': out += '\t'; break;
            case 'u':
            {
                int unicode = gmcp_read_hex_quad(data, pos);
                if (unicode >= 0xd800 && unicode <= 0xdbff)
                {
                    if (data.compare(pos, 2, "\\u") != 0)
                        return false;
                    pos += 2;
                    const int low = gmcp_read_hex_quad(data, pos);
                    if (low < 0xdc00 || low > 0xdfff)
                        return false;
                    unicode = 0x10000 + ((unicode - 0xd800) << 10) + low - 0xdc00;
                }
                if (unicode < 0 || (unicode >= 0xdc00 && unicode <= 0xdfff))
                    return false;

                // Cache strings are NUL-terminated.
                if (unicode == 0)
                    unicode = 0xfffd;
                if (unicode < 0x80)
                    out += static_cast<char>(unicode);
                else
                {
                    if (unicode >= 0x10000)
                        out += static_cast<char>(0xf0 | (unicode >> 18));
                    if (unicode >= 0x800)
                        out += static_cast<char>((unicode < 0x10000 ? 0xe0 : 0x80) | ((unicode >> 12) & 0x3f));
                    out += static_cast<char>((unicode < 0x800 ? 0xc0 : 0x80) | ((unicode >> 6) & 0x3f));
                    out += static_cast<char>(0x80 | (unicode & 0x3f));
                }
                break;
            }
            default: return false;
        }
    }
    return false;
}

static bool gmcp_extract_json_string(const std::string &data, const std::string &key, std::string &out)
{
    size_t pos = data.find('"');
    while (pos != std::string::npos)
    {
        std::string token;
        if (!gmcp_read_json_string(data, pos, token))
            return false;
        const size_t next = gmcp_skip_whitespace(data, pos);
        if (token == key && next < data.size() && data[next] == ':')
        {
            pos = gmcp_skip_whitespace(data, next + 1);
            return gmcp_read_json_string(data, pos, out);
        }
        pos = data.find('"', pos);
    }
    return false;
}

using GmcpPackage = std::pair<std::string, int>;

static bool gmcp_parse_supports(const std::string &data, std::vector<GmcpPackage> &packages)
{
    size_t pos = gmcp_skip_whitespace(data, 0);
    if (pos == data.size() || data[pos++] != '[')
        return false;
    pos = gmcp_skip_whitespace(data, pos);
    if (pos < data.size() && data[pos] == ']')
        return gmcp_skip_whitespace(data, pos + 1) == data.size();

    do
    {
        std::string token;
        if (!gmcp_read_json_string(data, pos, token))
            return false;
        token = gmcp_trim_token(token);
        const size_t split = token.find_first_of(gmcp_whitespace);
        const std::string package = token.substr(0, split);
        if (package.empty() || package.size() >= sizeof(DESCRIPTOR_DATA::gmcp_cap_t::name)
            || package.find_first_not_of("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.") != std::string::npos)
            return false;

        int version = 1;
        if (split != std::string::npos)
        {
            const std::string raw = gmcp_trim_token(token.substr(split));
            char *end;
            errno = 0;
            const long parsed = strtol(raw.c_str(), &end, 10);
            if (end == raw.c_str() || *end || errno == ERANGE || parsed <= 0 || parsed > INT_MAX)
                return false;
            version = static_cast<int>(parsed);
        }
        packages.emplace_back(package, version);
        pos = gmcp_skip_whitespace(data, pos);
        if (pos < data.size() && data[pos] == ']')
            return gmcp_skip_whitespace(data, pos + 1) == data.size();
        if (pos == data.size() || data[pos++] != ',')
            return false;
        pos = gmcp_skip_whitespace(data, pos);
    } while (pos < data.size());

    return false;
}

static void gmcp_subscribe(DESCRIPTOR_DATA *d, const std::string &package, int version)
{
    for (int i = 0; i < d->gmcp_cap_count; ++i)
    {
        if (package == d->gmcp_caps[i].name)
        {
            d->gmcp_caps[i].version = version;
            return;
        }
    }
    if (d->gmcp_cap_count == MAX_GMCP_CAPS)
        return;

    auto &entry = d->gmcp_caps[d->gmcp_cap_count++];
    memcpy(entry.name, package.c_str(), package.size() + 1);
    entry.version = version;
}

void gmcp_core_ping(DESCRIPTOR_DATA *d, const std::string &data)
{
    (void)data;
    if (!d)
        return;

    send_gmcp(d, "Core.Ping {}");
}

void gmcp_core_hello(DESCRIPTOR_DATA *d, const std::string &data)
{
    std::string client;
    std::string version;

    if (!d)
        return;

    if (gmcp_extract_json_string(data, "client", client))
        gmcp_cache_set(d, "Client.Name", client);

    if (gmcp_extract_json_string(data, "version", version))
        gmcp_cache_set(d, "Client.Version", version);
}

void gmcp_sendf(DESCRIPTOR_DATA *d, const std::string &module, const char *fmt, ...)
{
    if (!d || d->closing || d->descriptor < 0 || !d->gmcp_enabled || module.empty())
        return;

    if (!gmcp_is_subscribed(d, module))
        return;

    if (!fmt || !*fmt)
    {
        send_gmcp(d, module + " {}");
        return;
    }

    va_list args;
    va_start(args, fmt);
    va_list measure;
    va_copy(measure, args);
    const int length = vsnprintf(nullptr, 0, fmt, measure);
    va_end(measure);

    if (length >= 0 && length <= 49000)
    {
        std::vector<char> json(static_cast<size_t>(length) + 1);
        vsnprintf(json.data(), json.size(), fmt, args);
        send_gmcp(d, module + " " + std::string(json.data(), length));
    }
    va_end(args);
}

void gmcp_send_all(void (*func)(DESCRIPTOR_DATA *))
{
    if (!func)
        return;

    std::vector<DESCRIPTOR_DATA *> targets;
    for (DESCRIPTOR_DATA *d = first_descriptor; d; d = d->next)
    {
        if (!d->closing && !d->write_failed && d->descriptor >= 0 && d->connected == CON_PLAYING && d->gmcp_enabled)
            targets.push_back(d);
    }

    for (DESCRIPTOR_DATA *d : targets)
    {
        if (gmcp_desc_alive(d) && d->connected == CON_PLAYING && d->gmcp_enabled)
            func(d);
    }
}

void gmcp_core_supports_set(DESCRIPTOR_DATA *d, const std::string &data)
{
    std::vector<GmcpPackage> packages;
    if (!d || !gmcp_parse_supports(data, packages))
        return;

    d->gmcp_cap_count = 0;
    for (const auto &package : packages)
        gmcp_subscribe(d, package.first, package.second);
    d->gmcp_pending |= GMCP_PEND_VITALS | GMCP_PEND_ROOM | GMCP_PEND_STATUS | GMCP_PEND_STATS;
}

void gmcp_force_resync(CHAR_DATA *ch)
{
    if (!ch || !gmcp_desc_alive(ch->desc))
        return;

    DESCRIPTOR_DATA *d = ch->desc;

    gmcp_evt_char_status(ch);
    gmcp_evt_char_vitals(ch);
    gmcp_evt_char_stats(ch);
    gmcp_evt_room_change(ch);

    d->gmcp_pending |= GMCP_PEND_VITALS | GMCP_PEND_ROOM | GMCP_PEND_STATUS | GMCP_PEND_STATS;
    gmcp_flush(d);
}

void gmcp_evt_char_stats(CHAR_DATA *ch)
{
    if (!ch || !ch->desc)
        return;

    DESCRIPTOR_DATA *d = ch->desc;

    bool changed = false;

    changed |= gmcp_cache_set_int(d, "Char.Stats.str", get_curr_str(ch));
    changed |= gmcp_cache_set_int(d, "Char.Stats.dex", get_curr_dex(ch));
    changed |= gmcp_cache_set_int(d, "Char.Stats.con", get_curr_con(ch));
    changed |= gmcp_cache_set_int(d, "Char.Stats.int", get_curr_int(ch));
    changed |= gmcp_cache_set_int(d, "Char.Stats.wis", get_curr_wis(ch));
    changed |= gmcp_cache_set_int(d, "Char.Stats.cha", get_curr_cha(ch));
    changed |= gmcp_cache_set_int(d, "Char.Stats.hitroll", GET_HITROLL(ch));
    changed |= gmcp_cache_set_int(d, "Char.Stats.damroll", GET_DAMROLL(ch));
    changed |= gmcp_cache_set_int(d, "Char.Stats.armor", GET_AC(ch));
    changed |= gmcp_cache_set_int(d, "Char.Stats.align", ch->alignment);
    changed |= gmcp_cache_set_int(d, "Char.Stats.weight", ch->carry_weight);
    changed |= gmcp_cache_set_int(d, "Char.Stats.maxweight", can_carry_w(ch));
    changed |= gmcp_cache_set_int(d, "Char.Stats.numitems", ch->carry_number);
    changed |= gmcp_cache_set_int(d, "Char.Stats.maxnumitems", can_carry_n(ch));

    if (!changed)
        return;

    d->gmcp_pending |= GMCP_PEND_STATS;
}

void gmcp_evt_char_status(CHAR_DATA *ch)
{
    if (!ch || !ch->desc || IS_NPC(ch) || !ch->pcdata)
        return;

    DESCRIPTOR_DATA *d = ch->desc;

    bool changed = false;
    const char *cname = ch->name ? ch->name : "";
    const char *ctitle = ch->pcdata->title ? ch->pcdata->title : "";
    const char *cclass = (ch->main_ability >= 0 && ch->main_ability < MAX_ABILITY && ability_name[ch->main_ability])
                         ? ability_name[ch->main_ability]
                         : "unknown";
    const char *crace_raw = get_race(ch);
    const std::string crace = crace_raw ? capitalize(crace_raw) : "Unknown";
    const char *cclan = (ch->pcdata->clan && ch->pcdata->clan->name) ? ch->pcdata->clan->name : "None";

    changed |= gmcp_cache_set(d, "Char.Status.name", cname);
    changed |= gmcp_cache_set(d, "Char.Status.fullname", ctitle);
    changed |= gmcp_cache_set(d, "Char.Status.class", cclass);
    changed |= gmcp_cache_set(d, "Char.Status.race", crace);
    changed |= gmcp_cache_set_int(d, "Char.Status.wimpy", ch->wimpy);
    changed |= gmcp_cache_set(d, "Char.Status.wanted", bitset_to_string(ch->pcdata->wanted_flags, planet_flags));

    changed |= gmcp_cache_set(d, "Char.Status.clan", cclan);
    changed |= gmcp_cache_set_int(d, "Char.Status.salary", (ch->pcdata->clan ? ch->pcdata->salary : 0));
    changed |= gmcp_cache_set_int(d, "Char.Status.pkills", (ch->pcdata->clan ? ch->pcdata->clan->pkills : 0));
    changed |= gmcp_cache_set_int(d, "Char.Status.pdeaths", (ch->pcdata->clan ? ch->pcdata->clan->pdeaths : 0));

    if (IS_IMMORTAL(ch))
    {
        const char *bamfin = (ch->pcdata->bamfin && ch->pcdata->bamfin[0] != '\0')
                             ? ch->pcdata->bamfin
                             : "%s appears in a swirling mist.";
        const char *bamfout = (ch->pcdata->bamfout && ch->pcdata->bamfout[0] != '\0')
                              ? ch->pcdata->bamfout
                              : "%s appears in a swirling mist.";

        changed |= gmcp_cache_set_int(d, "Char.Status.trust", get_trust(ch));
        changed |= gmcp_cache_set_int(d, "Char.Status.wizinvis", (BV_IS_SET(ch->act, PLR_WIZINVIS) ? ch->pcdata->wizinvis : 0));
        changed |= gmcp_cache_set(d, "Char.Status.Bamfin", bamfin);
        changed |= gmcp_cache_set(d, "Char.Status.Bamfout", bamfout);
        if (ch->pcdata->area)
        {
            changed |= gmcp_cache_set(d, "Char.Status.AreaLoaded", (IS_SET (ch->pcdata->area->status, AREA_LOADED)) ? "yes" : "no");
            changed |= gmcp_cache_set_int(d, "Char.Status.LRVnum", ch->pcdata->area->low_r_vnum);
            changed |= gmcp_cache_set_int(d, "Char.Status.HRVnum", ch->pcdata->area->hi_r_vnum);            
            changed |= gmcp_cache_set_int(d, "Char.Status.LOVnum", ch->pcdata->area->low_o_vnum);            
            changed |= gmcp_cache_set_int(d, "Char.Status.HOVnum", ch->pcdata->area->hi_o_vnum);            
            changed |= gmcp_cache_set_int(d, "Char.Status.LMVnum", ch->pcdata->area->low_m_vnum);            
            changed |= gmcp_cache_set_int(d, "Char.Status.HMVnum", ch->pcdata->area->hi_m_vnum);

        }
    }    

    if (!changed)
        return;
    d->gmcp_pending |= GMCP_PEND_STATUS;
}

void gmcp_evt_char_vitals(CHAR_DATA *ch)
{
    if (!ch || !ch->desc)
        return;

    DESCRIPTOR_DATA *d = ch->desc;

    bool changed = false;

    changed |= gmcp_cache_set_int(d, "Char.Vitals.hp", ch->hit);
    changed |= gmcp_cache_set_int(d, "Char.Vitals.maxhp", ch->max_hit);
    changed |= gmcp_cache_set_int(d, "Char.Vitals.mana", ch->mana);
    changed |= gmcp_cache_set_int(d, "Char.Vitals.maxmana", ch->max_mana);
    changed |= gmcp_cache_set_int(d, "Char.Vitals.move", ch->move);
    changed |= gmcp_cache_set_int(d, "Char.Vitals.maxmove", ch->max_move);
    changed |= gmcp_cache_set_int(d, "Char.Vitals.credits", ch->gold);
    changed |= gmcp_cache_set_int(d, "Char.Vitals.bank", (!IS_NPC(ch) && ch->pcdata) ? ch->pcdata->bank : 0);
    changed |= gmcp_cache_set_int(d, "Char.Vitals.pkills", (!IS_NPC(ch) && ch->pcdata) ? ch->pcdata->pkills : 0);
    changed |= gmcp_cache_set_int(d, "Char.Vitals.mkills", (!IS_NPC(ch) && ch->pcdata) ? ch->pcdata->mkills : 0);

    if (!changed)
        return;
    d->gmcp_pending |= GMCP_PEND_VITALS;
    //gmcp_send_from_cache(d, "Char.Vitals", build_char_vitals);
}

void gmcp_evt_room_change(CHAR_DATA *ch)
{
    if (!ch || !ch->desc || !ch->in_room)
        return;

    DESCRIPTOR_DATA *d = ch->desc;
    ROOM_INDEX_DATA *room = ch->in_room;

    bool changed = false;

    changed |= gmcp_cache_set(d, "Room.Info.num", std::to_string(room->vnum));
    changed |= gmcp_cache_set(d, "Room.Info.name", room->name);
    changed |= gmcp_cache_set_int(d, "Room.Info.rentship",
        (BV_IS_SET(room->room_flags, ROOM_CAN_LAND) ? 1 : 0));
    changed |= gmcp_cache_set_int(d, "Room.Info.serinstop",
        (is_bus_stop(ch->in_room->vnum) ? 1 : 0));

    if (!changed)
        return;

    d->gmcp_pending |= GMCP_PEND_ROOM;
    //gmcp_send_from_cache(d, "Room.Info", build_room_info);
}

bool gmcp_is_subscribed(DESCRIPTOR_DATA *d, const std::string &module)
{
    if (!d)
        return false;
    for (int i = 0; i < d->gmcp_cap_count; ++i)
    {
        const char *name = d->gmcp_caps[i].name;
        const size_t length = strlen(name);
        if (module.compare(0, length, name) == 0
            && (module.size() == length || module[length] == '.'))
            return true;
    }
    return false;
}

void gmcp_core_supports_add(DESCRIPTOR_DATA *d, const std::string &data)
{
    std::vector<GmcpPackage> packages;
    if (!d || !gmcp_parse_supports(data, packages))
        return;

    for (const auto &package : packages)
        gmcp_subscribe(d, package.first, package.second);
    d->gmcp_pending |= GMCP_PEND_VITALS | GMCP_PEND_ROOM | GMCP_PEND_STATUS | GMCP_PEND_STATS;
}

void gmcp_core_supports_remove(DESCRIPTOR_DATA *d, const std::string &data)
{
    std::vector<GmcpPackage> packages;
    if (!d || !gmcp_parse_supports(data, packages))
        return;

    for (const auto &package : packages)
    {
        for (int i = 0; i < d->gmcp_cap_count; ++i)
        {
            if (package.first == d->gmcp_caps[i].name)
            {
                d->gmcp_caps[i] = d->gmcp_caps[--d->gmcp_cap_count];
                break;
            }
        }
    }
}

void gmcp_init_subscriptions(DESCRIPTOR_DATA *d)
{
    if (!d)
        return;

    d->gmcp_cap_count = 0;
    gmcp_subscribe(d, "Char", 1);
    gmcp_subscribe(d, "Room", 1);
}

void gmcp_flush(DESCRIPTOR_DATA *d)
{
    if (!gmcp_desc_alive(d))
        return;

    if (!d->gmcp_enabled)
    {
        d->gmcp_pending = 0;
        return;
    }

    if (d->gmcp_pending == 0)
        return;

    /* IMPORTANT:
     * We do NOT combine JSON.
     * We simply send multiple GMCP messages in one burst.
     */
    const int pending = d->gmcp_pending;
    d->gmcp_pending = 0;

    if ((pending & GMCP_PEND_VITALS) && gmcp_desc_alive(d))
        gmcp_send_from_cache(d, "Char.Vitals", build_char_vitals);

    if ((pending & GMCP_PEND_ROOM) && gmcp_desc_alive(d))
        gmcp_send_from_cache(d, "Room.Info", build_room_info);

    if ((pending & GMCP_PEND_STATUS) && gmcp_desc_alive(d))
        gmcp_send_from_cache(d, "Char.Status", build_char_status);

    if ((pending & GMCP_PEND_STATS) && gmcp_desc_alive(d))
        gmcp_send_from_cache(d, "Char.Stats", build_char_stats);
}
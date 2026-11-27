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
*		            Bounty Hunter Module    			   *   
*                    (  and area capturing as well  )                      * 
****************************************************************************/

#include <sys/types.h>
#include <ctype.h>
#include <stdio.h>
#include <string.h>
/* #include <stdlib.h> */
#include <time.h>
#include <charconv>
#include <climits>
#include <fstream>
#include <unistd.h>
#include <fcntl.h>
#include <iomanip>
#include <sys/stat.h>
#include <openssl/rand.h>
#include "mud.h"


BOUNTY_DATA * first_bounty;
BOUNTY_DATA * last_bounty;
BOUNTY_DATA * first_disintigration;
BOUNTY_DATA * last_disintigration;


int xp_compute( CHAR_DATA *ch, CHAR_DATA *victim );

static constexpr const char *bounty_path = SYSTEM_DIR DISINTIGRATION_LIST;
static constexpr const char *bounty_intent_path = SYSTEM_DIR "bounty-transaction";
static constexpr int bounty_intent_version = 1;
static GameContext *bounty_game = nullptr;

struct BountyIntent
{
    std::string transaction;
    std::string previous_transaction;
    std::string player;
    std::string target;
    long amount = 0;
    std::string poster;
};

static bool sync_bounty_directory()
{
    const int fd = open(SYSTEM_DIR, O_RDONLY | O_DIRECTORY);
    if (fd < 0)
        return false;
    const bool synced = fsync(fd) == 0;
    const bool closed = close(fd) == 0;
    return synced && closed;
}

static bool publish_bounty_file(const char *path, const std::string &contents)
{
    std::string temporary = std::string(path) + ".tmp.XXXXXX";
    const int fd = mkstemp(temporary.data());
    if (fd < 0)
        return false;
    size_t written = 0;
    while (written < contents.size())
    {
        const ssize_t result = write(fd, contents.data() + written, contents.size() - written);
        if (result < 0 && errno == EINTR)
            continue;
        if (result <= 0)
            break;
        written += result;
    }
    const bool flushed = written == contents.size() && fsync(fd) == 0;
    const bool closed = close(fd) == 0;
    if (flushed && closed && rename(temporary.c_str(), path) == 0)
        return sync_bounty_directory();
    unlink(temporary.c_str());
    return false;
}

static std::string bounty_list_contents(const BountyIntent *replacement, const BOUNTY_DATA *excluded)
{
    std::ostringstream out;
    for (const BOUNTY_DATA *bounty = first_disintigration; bounty; bounty = bounty->next)
        if (bounty != excluded && (!replacement || str_cmp(bounty->target, replacement->target)))
            out << bounty->target << '\n' << bounty->amount << '\n' << bounty->poster << '\n';
    if (replacement && replacement->amount > 0)
        out << replacement->target << '\n' << replacement->amount << '\n' << replacement->poster << '\n';
    out << "$\n";
    return out.str();
}

static bool save_disintigrations(const BOUNTY_DATA *excluded = nullptr)
{
    return publish_bounty_file(bounty_path, bounty_list_contents(nullptr, excluded));
}

static bool valid_bounty_name(const std::string &name)
{
    return !name.empty() && name.size() <= 20
        && name.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ") == std::string::npos;
}

static bool valid_transaction(const std::string &transaction)
{
    return transaction.size() == 64
        && transaction.find_first_not_of("0123456789abcdef") == std::string::npos;
}

static bool clear_bounty_intent()
{
    if (unlink(bounty_intent_path) != 0 && errno != ENOENT)
        return false;
    return sync_bounty_directory();
}

static void free_bounty(BOUNTY_DATA *bounty);

static bool recover_bounty_transaction()
{
    std::ifstream input(bounty_intent_path);
    if (!input)
    {
        struct stat status;
        if (stat(bounty_intent_path, &status) == 0 || errno != ENOENT)
            return false;
        return sync_bounty_directory();
    }

    BountyIntent intent;
    int version = 0;
    if (!(input >> version >> std::quoted(intent.transaction) >> std::quoted(intent.previous_transaction)
        >> std::quoted(intent.player) >> std::quoted(intent.target) >> intent.amount >> std::quoted(intent.poster)))
        return false;
    input >> std::ws;
    if (!input.eof() || version != bounty_intent_version || !valid_transaction(intent.transaction)
        || (!intent.previous_transaction.empty() && !valid_transaction(intent.previous_transaction))
        || !valid_bounty_name(intent.player) || !valid_bounty_name(intent.target)
        || !valid_bounty_name(intent.poster) || intent.amount < 0 || intent.amount > INT_MAX)
        return false;

    std::string saved_transaction;
    if (!read_saved_bounty_transaction(intent.player, saved_transaction))
        return false;
    if (saved_transaction == intent.previous_transaction)
        return clear_bounty_intent();
    if (saved_transaction != intent.transaction)
        return false;
    if (!publish_bounty_file(bounty_path, bounty_list_contents(&intent, nullptr)))
        return false;

    BOUNTY_DATA *bounty = get_disintigration(intent.target.data());
    if (intent.amount == 0)
    {
        if (bounty)
            free_bounty(bounty);
    }
    else if (bounty)
    {
        bounty->amount = intent.amount;
        STRFREE(bounty->poster);
        bounty->poster = STRALLOC(intent.poster);
    }
    else
    {
        CREATE(bounty, BOUNTY_DATA, 1);
        bounty->game = bounty_game;
        bounty->target = STRALLOC(intent.target);
        bounty->amount = intent.amount;
        bounty->poster = STRALLOC(intent.poster);
        LINK(bounty, first_disintigration, last_disintigration, next, prev);
    }
    return clear_bounty_intent();
}

static bool commit_bounty_transaction(CHAR_DATA *ch, const std::string &target,
                                      long amount, const std::string &poster, int balance)
{
    std::string saved_transaction;
    if (!read_saved_bounty_transaction(ch->name, saved_transaction)
        && (!save_char_obj(ch) || !read_saved_bounty_transaction(ch->name, saved_transaction)))
        return false;
    if (saved_transaction != ch->pcdata->bounty_transaction)
        return false;

    unsigned char random[32];
    if (RAND_bytes(random, sizeof(random)) != 1)
        return false;
    static constexpr char hex[] = "0123456789abcdef";
    std::string transaction;
    for (unsigned char byte : random)
    {
        transaction += hex[byte >> 4];
        transaction += hex[byte & 15];
    }
    std::ostringstream out;
    out << bounty_intent_version << ' ' << std::quoted(transaction) << ' ' << std::quoted(ch->pcdata->bounty_transaction)
        << ' ' << std::quoted(std::string(ch->name)) << ' ' << std::quoted(target)
        << ' ' << amount << ' ' << std::quoted(poster) << '\n';
    if (!publish_bounty_file(bounty_intent_path, out.str()))
        return false;

    const int previous_gold = ch->gold;
    const std::string previous_transaction = ch->pcdata->bounty_transaction;
    ch->gold = balance;
    ch->pcdata->bounty_transaction = transaction;
    if (!save_char_obj(ch))
    {
        ch->gold = previous_gold;
        ch->pcdata->bounty_transaction = previous_transaction;
        clear_bounty_intent();
        return false;
    }

    if (!read_saved_bounty_transaction(ch->name, saved_transaction))
        return false;
    if (saved_transaction == previous_transaction)
    {
        ch->gold = previous_gold;
        ch->pcdata->bounty_transaction = previous_transaction;
        clear_bounty_intent();
        return false;
    }
    if (saved_transaction != transaction)
        return false;
    return recover_bounty_transaction();
}

static bool is_bounty_hunter( const CHAR_DATA *ch )
{
    if (!ch || IS_NPC(ch) || !ch->pcdata || !ch->pcdata->clan)
        return false;
    const char *name = ch->pcdata->clan->name;
    return name && (!str_cmp(name, "the hunters guild")
        || !str_cmp(name, "The Bounty Hunter's Guild") || !str_cmp(name, "the assassins guild"));
}

static void free_bounty( BOUNTY_DATA *bounty )
{
    UNLINK(bounty, first_disintigration, last_disintigration, next, prev);
    STRFREE(bounty->target);
    STRFREE(bounty->poster);
    DISPOSE(bounty);
}

bool is_disintigration( CHAR_DATA *victim )
{
    BOUNTY_DATA *bounty;

    for ( bounty = first_disintigration; bounty; bounty = bounty->next )
    if ( !str_cmp( victim->name , bounty->target ) )
             return TRUE;
    return FALSE;                           
}

BOUNTY_DATA *get_disintigration( char *target )
{
    BOUNTY_DATA *bounty;
    
    for ( bounty = first_disintigration; bounty; bounty = bounty->next )
       if ( !str_cmp( target, bounty->target ) )
         return bounty;
    return NULL;
}

void load_bounties( GameContext *game )
{
    bounty_game = game;
    first_disintigration = last_disintigration = nullptr;
    log_string("Loading disintigrations...");

    const char *path = SYSTEM_DIR DISINTIGRATION_LIST;
    std::ifstream list(path);
    if (!list)
    {
        perror(path);
        exit(EXIT_FAILURE);
    }

    std::string target, amount_text, poster;
    while (list >> target && target != "$")
    {
        if (!(list >> amount_text >> poster) || amount_text == "$" || poster == "$")
        {
            bug("Incomplete record in %s for %s", path, target.c_str());
            break;
        }
        long amount = 0;
        const char *end = amount_text.data() + amount_text.size();
        const auto parsed = std::from_chars(amount_text.data(), end, amount);
        if (parsed.ec != std::errc{} || parsed.ptr != end || amount <= 0)
        {
            bug("Invalid bounty amount in %s for %s", path, target.c_str());
            continue;
        }

        BOUNTY_DATA *bounty;
        CREATE(bounty, BOUNTY_DATA, 1);
        bounty->game = game;
        bounty->target = STRALLOC(target);
        bounty->amount = std::min<long>(amount, INT_MAX);
        bounty->poster = STRALLOC(poster);
        LINK(bounty, first_disintigration, last_disintigration, next, prev);
    }
    if (!recover_bounty_transaction())
    {
        bug("Bounty transaction recovery requires repair; refusing startup");
        exit(EXIT_FAILURE);
    }
    log_string(" Done bounties ");
}

void do_bounties( CHAR_DATA *ch, char *argument )
{
    BOUNTY_DATA *bounty;

    if (!recover_bounty_transaction())
    {
        send_to_char("Bounty records are awaiting recovery. Please try again later.\n", ch);
        return;
    }
    int count = 0;
    if (get_trust(ch) < LEVEL_IMMORTAL && !is_bounty_hunter(ch))
    {    send_to_char( "\nOnly hunters can access that information!\n", ch );
        return;
    }
    set_char_color( AT_WHITE, ch );
    send_to_char( "\nBounty                      Amount          Poster\n", ch );
    for ( bounty = first_disintigration; bounty; bounty = bounty->next )
    {
        set_char_color( AT_RED, ch );
        ch_printf( ch, "%-26s %-14ld %-20s\n", bounty->target, bounty->amount, bounty->poster );
        count++;
    }

    if ( !count )
    {
        set_char_color( AT_GREY, ch );
        send_to_char( "There are no bounties set at this time.\n", ch );
	return;
    }
}

static void post_bounty( CHAR_DATA *ch, CHAR_DATA *victim, int amount )
{
    if (!recover_bounty_transaction())
    {
        send_to_char("Bounty records are awaiting recovery. Please try again later.\n", ch);
        return;
    }
    BOUNTY_DATA *bounty = get_disintigration(victim->name);
    if (bounty && amount > INT_MAX - bounty->amount)
    {
        send_to_char("The bounty fund cannot accept that much.\n", ch);
        return;
    }

    const long total = (bounty ? bounty->amount : 0) + amount;
    const std::string poster = bounty ? bounty->poster : ch->name;
    if (!commit_bounty_transaction(ch, victim->name, total, poster, ch->gold - amount))
    {
        send_to_char("Your bounty payment is pending recovery. Please try again later.\n", ch);
        return;
    }

    const std::string message = str_printf("&R%s has added %d credits to the bounty on %s.\n",
        ch->name, amount, victim->name);
    send_to_char(message, ch);
    for (CHAR_DATA *recipient = first_char; recipient; recipient = recipient->next)
    {
        if (recipient != ch && !IS_NPC(recipient)
            && (is_bounty_hunter(recipient) || get_trust(recipient) >= LEVEL_IMMORTAL))
            send_to_char(message, recipient);
        if (recipient == victim)
            ch_printf(recipient, "&RSomeone has added %d credits to the bounty on you!\n", amount);
    }
}

void do_addbounty( CHAR_DATA *ch, char *argument )
{
    std::string arg;
    std::string argstr = argument ? argument : "";
    int amount = 0;
    CHAR_DATA *victim;

    if (!ch || IS_NPC(ch) || !ch->name)
        return;
             
    if ( argstr.empty() )
    {
         do_bounties( ch , argument );
         return;
    }

    argstr = one_argument(argstr, arg);
    
    if (argstr.empty() )
    {
    	send_to_char( "Usage: Addbounty <target> <amount>\n", ch );
    	return;
    }

    if ( is_bounty_hunter(ch) )
    {
    	send_to_char( "Your job is to collect bounties not post them.", ch );
    	return;
    }

    /* Can now add bountys anywhere -- Kre
    if ( !ch->in_room || ch->in_room->vnum != 6604 )
    {
    	send_to_char( "You will have to go to the Guild on Tatooine to add a new bounty.", ch );
    	return;
    }
    */
    std::string amount_text;
    argstr = one_argument(argstr, amount_text);
    const char *end = amount_text.data() + amount_text.size();
    const auto parsed = std::from_chars(amount_text.data(), end, amount);
    if (!argstr.empty() || parsed.ec != std::errc{} || parsed.ptr != end || amount < 5000)
     {
    	send_to_char( "A bounty should be at least 5000 credits.\n", ch );
    	return;
    }

    if ( !(victim = get_char_world( ch, arg )) )
    {
        send_to_char( "They don't appear to be here .. wait til they log in.\n", ch );
        return;
    }

    if ( IS_NPC(victim) )
    {
    	send_to_char( "You can only set bounties on other players .. not mobs!\n", ch );
	return;
    }
    if ( is_bounty_hunter(victim) )
    {
      send_to_char( "&RYou can not post bounties on bounty hunters!\n", ch);
      return;
    }

    if (ch->gold < amount)
    {
    	send_to_char( "You don't have that many credits!\n", ch );
    	return;
    }
    post_bounty(ch, victim, amount);
}

void remove_disintigration( BOUNTY_DATA *bounty )
{
    if (!bounty)
        return;
    std::string target = bounty->target;
    if (!recover_bounty_transaction())
        return;
    bounty = get_disintigration(target.data());
    if (bounty && save_disintigrations(bounty))
        free_bounty(bounty);
}

void claim_disintigration( CHAR_DATA *ch, CHAR_DATA *victim )
{
    if (!ch || !victim || ch == victim || IS_NPC(victim))
        return;

    if (!recover_bounty_transaction())
    {
        send_to_char("Bounty records are awaiting recovery. Please try again later.\n", ch);
        return;
    }
    BOUNTY_DATA *bounty = get_disintigration(victim->name);
    const int level = ch->skill_level[HUNTING_ABILITY];
    const long exp_limit = std::max(1, exp_level(level + 1) - exp_level(level));
    if (!bounty || !is_bounty_hunter(ch))
    {
        if (!IS_NPC(ch) && BV_IS_SET(victim->act, PLR_KILLER))
        {
            const long exp = std::clamp<long>(xp_compute(ch, victim), 1, exp_limit);
            gain_exp(ch, exp, HUNTING_ABILITY);
            set_char_color(AT_BLOOD, ch);
            ch_printf(ch, "You receive %ld hunting experience for executing a wanted killer.\n", exp);
        }
        else if (!IS_NPC(ch))
        {
            BV_SET_BIT(ch->act, PLR_KILLER);
            ch_printf(ch, "You are now wanted for the murder of %s.\n", victim->name);
        }
        return;
    }

    const int previous_gold = ch->gold;
    const int balance = static_cast<int>(std::min<long long>(static_cast<long long>(previous_gold)
        + bounty->amount, INT_MAX));
    const long exp = static_cast<long>(std::clamp<long long>(
        static_cast<long long>(bounty->amount) + xp_compute(ch, victim), 1, exp_limit));
    const std::string target = bounty->target;
    const std::string poster = bounty->poster;
    if (!commit_bounty_transaction(ch, target, 0, poster, balance))
    {
        send_to_char("Your bounty payment is pending recovery. Please try again later.\n", ch);
        return;
    }

    gain_exp(ch, exp, HUNTING_ABILITY);
    set_char_color(AT_BLOOD, ch);
    ch_printf(ch, "You receive %ld experience and %d credits,\n from the bounty on %s\n",
        exp, balance - previous_gold, target.c_str());
    echo_to_all(AT_RED, str_printf("The disintegration bounty on %s has been claimed!", victim->name), 0);
    if (!BV_IS_SET(victim->act, PLR_KILLER))
        BV_SET_BIT(ch->act, PLR_KILLER);
}

void do_rembounty(  CHAR_DATA *ch, char *argument )
{
  if (!recover_bounty_transaction())
  {
    send_to_char("Bounty records are awaiting recovery. Please try again later.\n", ch);
    return;
  }
  BOUNTY_DATA *bounty = get_disintigration( argument );
  
  if ( bounty != NULL )
    remove_disintigration(bounty);
  return;

}  

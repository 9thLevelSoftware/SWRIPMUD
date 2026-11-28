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
*			 Command interpretation module			   *
****************************************************************************/

#include <sys/types.h>
#include <sys/time.h>
#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "mud.h"
#include "olc.h"

/*
 * Externals
 */

void subtract_times( struct timeval *etime, struct timeval *stime );
bool olc_try_inline_interpret(CHAR_DATA* ch, const std::string& command, const std::string& argument, OlcInterpretStage stage);
bool craft_interpret( CHAR_DATA *ch, const std::string& argument );


bool	check_social	args( ( CHAR_DATA *ch, const std::string& command,
			    const std::string& argument ) );


/*
 * Log-all switch.
 */
bool				fLogAll		= FALSE;


CMDTYPE	   *command_hash[COMMAND_HASH_BUCKETS];	/* hash table for cmd_table */
SOCIALTYPE *social_index[27];   /* hash table for socials   */

/*
 * Character not in position for command?
 */
bool check_pos( CHAR_DATA *ch, sh_int position )
{
    if ( ch->position < position )
    {
	switch( ch->position )
	{
	case POS_DEAD:
	    send_to_char( "A little difficult to do when you are DEAD...\n", ch );
	    break;

	case POS_MORTAL:
	case POS_INCAP:
	    send_to_char( "You are hurt far too bad for that.\n", ch );
	    break;

	case POS_STUNNED:
	    send_to_char( "You are too stunned to do that.\n", ch );
	    break;

	case POS_SLEEPING:
	    send_to_char( "In your dreams, or what?\n", ch );
	    break;

	case POS_RESTING:
	    send_to_char( "Nah... You feel too relaxed...\n", ch);
	    break;

	case POS_SITTING:
	    send_to_char( "You can't do that sitting down.\n", ch);
	    break;

	case POS_FIGHTING:
	    send_to_char( "No way!  You are still fighting!\n", ch);
	    break;

	}
	return FALSE;
    }
    return TRUE;
}

extern char lastplayercmd[MAX_INPUT_LENGTH*2];

/*
 * The main entry point for executing commands.
 * Can be recursively called from 'at', 'order', 'force'.
 */

char multicommand[MAX_INPUT_LENGTH]; 

std::string parse_target( CHAR_DATA *ch, const std::string &oldstring )
{
    std::string result;

    if ( !ch || !ch->pcdata )
        return oldstring;

    result.reserve( oldstring.size() );

    const char *str = oldstring.c_str();
    int count = 0;

    while ( *str != '\0' )
    {
        if ( *str != '$' )
        {
            int len = utf8_char_len_safe( str );

            if ( count + len >= MAX_INPUT_LENGTH )
                break;

            result.append( str, len );
            str += len;
            count += len;
            continue;
        }

        ++str;

        if ( *str == '$' && ch->pcdata->target[0] != '\0' )
        {
            const char *i = ch->pcdata->target;

            while ( *i != '\0' )
            {
                int len = utf8_char_len_safe( i );

                if ( count + len >= MAX_INPUT_LENGTH )
                {
                    send_to_char( "Target substitution too long; not processed.\n", ch );
                    return oldstring;
                }

                result.append( i, len );
                i += len;
                count += len;
            }

            ++str;
        }
        else
        {
            if ( count + 1 >= MAX_INPUT_LENGTH )
                break;

            result.push_back( '$' );
            ++count;
        }
    }

    return result;
}
 
 std::string get_multi_command( DESCRIPTOR_DATA *d, const std::string &argument )
{
    std::string first;
    std::string remainder;

    first.reserve( argument.size() );

    for ( size_t i = 0; i < argument.size(); ++i )
    {
        if ( argument[i] == '|' )
        {
            /* Escaped || becomes literal | */
            if ( i + 1 < argument.size() && argument[i + 1] == '|' )
            {
                first.push_back( '|' );
                ++i;
                continue;
            }

            /* Single | splits command */
            remainder = argument.substr( i + 1 );
            if ( d )
                d->incomm_str = remainder;
            return first;
        }

        first.push_back( argument[i] );
    }

    if ( d )
        d->incomm_str.clear();

    return first;
}

bool command_is_authorized_for_char(CHAR_DATA* ch, CMDTYPE* cmd)
{
    int trust;

    if (!ch || !cmd)
        return false;

    trust = get_trust(ch);

    if (cmd->level <= trust
    && (IS_NPC(ch)
    || !ch->pcdata
    || !cmd->commandgroup.any()
    || cmd->commandgroup.intersects(ch->pcdata->commandgroup)))
        return true;

    if (!IS_NPC(ch)
    && ch->pcdata
    && ch->pcdata->bestowments
    && ch->pcdata->bestowments[0] != '\0'
    && is_name(cmd->name, ch->pcdata->bestowments)
    && cmd->level <= (trust + 5))
        return true;

    return false;
}

static inline void call_do_fun_str( DO_FUN *fun, CHAR_DATA *ch, const std::string &arg )
{
    std::vector<char> buf( arg.begin(), arg.end() );
    buf.push_back( '\0' );
    ( *fun )( ch, buf.data() );
}

void interpret( CHAR_DATA *ch, const std::string &argument )
{
    std::string arg = argument;
    std::string command;
    std::string logline;
    TIMER *timer = NULL;
    CMDTYPE *cmd = NULL;
    int loglvl;
    bool found;
    struct timeval time_used;
    long tmptime;


    if ( !ch )
    {
	bug( "interpret: null ch!" );
	return;
    }

    found = FALSE;
    if ( ch->substate == SUB_REPEATCMD )
    {
        DO_FUN *fun;

        if ( (fun=ch->last_cmd) == NULL )
        {
            ch->substate = SUB_NONE;
            bug( "interpret: SUB_REPEATCMD with NULL last_cmd" );
            return;
        }
        else
        {
            int x;

            /*
            * yes... we lose out on the hashing speediness here...
            * but the only REPEATCMDS are wizcommands (currently)
            */
            for ( x = 0; x < 126; x++ )
            {
                for ( cmd = command_hash[x]; cmd; cmd = cmd->next )
                if ( cmd->do_fun == fun )
                    {
                        found = TRUE;
                        break;
                    }
                if ( found )
                    break;
            }
            if ( !found )
            {
                cmd = NULL;
                bug( "interpret: SUB_REPEATCMD: last_cmd invalid" );
                return;
            }
            logline = "(" + std::string( cmd->name ) + ") " + arg;
        }
    }

    if ( !cmd )
    {
        /* Changed the order of these ifchecks to prevent crashing. */
        if ( arg.empty() ) 
        {
            bug( "interpret: null argument!" );
            return;
        }

        /*
        * Strip leading spaces.
        */
        while ( !arg.empty() && isspace_utf8( arg.c_str() ) )
        {
            const char *p = arg.c_str();
            UTF8_NEXT( p );
            arg.erase( 0, p - arg.c_str() );
        }

        if ( arg.empty() )
            return;

        timer = get_timerptr( ch, TIMER_DO_FUN );

        /* BV_REMOVE_BIT( ch->affected_by, AFF_HIDE ); */

        /*
        * Implement freeze command.
        */
        if ( !IS_NPC(ch) && BV_IS_SET(ch->act, PLR_FREEZE) )
        {
            send_to_char( "You're totally frozen!\n", ch );
            return;
        }

        /*
        * Grab the command word.
        * Special parsing so ' can be a command,
        *   also no spaces needed after punctuation.
        */
        logline = arg;

        if ( ch->desc && arg.find( '|' ) != std::string::npos )
            arg = get_multi_command( ch->desc, arg ); 

        if ( !IS_NPC(ch) && ch->pcdata && ch->pcdata->target )
            if ( ch->pcdata->target[0] != '\0' )
                if ( arg.find( '$' ) != std::string::npos )
                    arg = parse_target( ch, arg );
    
        if ( !arg.empty()
        &&   static_cast<unsigned char>( arg[0] ) < 0x80
        &&   !isalpha( static_cast<unsigned char>( arg[0] ) )
        &&   !isdigit( static_cast<unsigned char>( arg[0] ) ) )
        {
            command.assign( 1, arg[0] );
            arg.erase( 0, 1 );
            while ( !arg.empty() && isspace_utf8( arg.c_str() ) )
            {
                const char *p = arg.c_str();
                UTF8_NEXT( p );
                arg.erase( 0, p - arg.c_str() );
            }
        }
        else
            arg = one_argument( arg, command );

        /*
        * Look for command in command table.
        * Check for council powers and/or bestowments
        */
        if ( !command.empty() )
        {
            for ( cmd = command_hash[command_hash_bucket(command.front())]; cmd; cmd = cmd->next )
                if ( !str_prefix( command.c_str(), cmd->name )
                &&   command_is_authorized_for_char(ch, cmd) )
                {
                    found = TRUE;
                    break;
                }
        }

        /*
        * Turn off afk bit when any command performed.
        */
        if ( BV_IS_SET ( ch->act, PLR_AFK)  && (str_cmp(command, "AFK")))
        {
            BV_REMOVE_BIT( ch->act, PLR_AFK );
                act( AT_GREY, "$n is no longer afk.", ch, NULL, NULL, TO_ROOM );
        }
    }

    /*
     * Log and snoop.
     */
    snprintf( lastplayercmd, MAX_INPUT_LENGTH * 2, "** %s: %s",
        ch->name ? ch->name : "(null)", logline.c_str() );

    if ( found && cmd->log == LOG_NEVER )
        logline = "XXXXXXXX XXXXXXXX XXXXXXXX";

    loglvl = found ? cmd->log : LOG_NORMAL;

    if ( ( !IS_NPC(ch) && BV_IS_SET(ch->act, PLR_LOG) )
    ||   fLogAll
    ||	 loglvl == LOG_BUILD
    ||   loglvl == LOG_HIGH
    ||   loglvl == LOG_ALWAYS )
    {
        /*
        * Make it so a 'log all' will send most output to the log
        * file only, and not spam the log channel to death	-Thoric
        */
        if ( fLogAll && loglvl == LOG_NORMAL
        &&  (IS_NPC(ch) || !BV_IS_SET(ch->act, PLR_LOG)) )
        loglvl = LOG_ALL;

      /* Added by Narn to show who is switched into a mob that executes
           a logged command.  Check for descriptor in case force is used. */
        if ( ch->desc && ch->desc->original ) 
          log_printf_plus( loglvl, ch->top_level, "Log %s (%s): %s",
                   ch->name ? ch->name : "(null)",
                   ch->desc->original->name ? ch->desc->original->name : "(null)",
                   logline.c_str() );
        else
          log_printf_plus( loglvl, ch->top_level, "Log %s: %s",
                   ch->name ? ch->name : "(null)", logline.c_str() );
    }

    if ( ch->desc && ch->desc->snoop_by && !ch->desc->snoop_by->closing && !ch->desc->snoop_by->write_failed )
    {
        output_to_descriptor( ch->desc->snoop_by, std::string( ch->name ? ch->name : "" ) );
        output_to_descriptor( ch->desc->snoop_by, "% " );
        output_to_descriptor( ch->desc->snoop_by, logline );
        output_to_descriptor( ch->desc->snoop_by, "\n" );
    }

    

    if ( timer )
    {
        if ( craft_interpret_during_build( ch, found && cmd->do_fun == do_craft ? "craft" : command, arg ) )
            return;

        int tempsub;

        tempsub = ch->substate;
        ch->substate = SUB_TIMER_DO_ABORT;
        call_do_fun_str( timer->do_fun, ch, "" );
        if ( char_died(ch) )
            return;
        if ( ch->substate != SUB_TIMER_CANT_ABORT )
        {
            ch->substate = tempsub;
            extract_timer( ch, timer );
        }
        else
        {
            ch->substate = tempsub;
            return;
        }
    }

    if ( ch->substate == SUB_CRAFT_MENU )
    {
        if (craft_interpret( ch, argument ))
            return;
    }

    if (olc_try_inline_interpret(ch, command, arg, OlcInterpretStage::EARLY))
        return;

    bool foundother = true;

    /*
     * Look for command in skill and socials table.
     */
    if ( !found && !command.empty() )
    {
        const bool skill_found = check_skill( ch, command, arg );
        if ( !skill_found && check_alias( ch, command, arg ) )
            return;
        if ( !skill_found && !check_social( ch, command, arg ) )
        {
            EXIT_DATA *pexit;
            foundother = false;
            /* check for an auto-matic exit command */
            if ( ch->in_room && (pexit = find_door( ch, command, TRUE )) != NULL
            &&   IS_SET( pexit->exit_info, EX_xAUTO ))
            {
                if ( IS_SET(pexit->exit_info, EX_CLOSED)
                && (!IS_AFFECTED(ch, AFF_PASS_DOOR)
                ||   IS_SET(pexit->exit_info, EX_NOPASSDOOR)) )
                {
                    if ( !IS_SET( pexit->exit_info, EX_SECRET ) )
                        act( AT_PLAIN, "The $d is closed.", ch, NULL, pexit->keyword, TO_CHAR );
                    else
                        send_to_char( "You cannot do that here.\n", ch );
                    return;
                }
                move_char( ch, pexit, 0, true );
                return;
            }
        }
    }

    if (olc_try_inline_interpret(ch, command, arg, OlcInterpretStage::LATE)) // Needs to be after the above as room inline editing allows for movement - DV 4-5-26
        return;

    if (!found )  // Need to move this return below the olc edit checking, otherwise if an olc 
    {                           // short command doesn't match something in the command table, it won't reach it - DV
        if ( !foundother )
            send_to_char( "Huh?\n", ch );
        return; 
    }

    /*
     * Character not in position for command?
     */
    if ( !check_pos( ch, cmd->position ) )
	return;
    
    /* Berserk check for flee.. maybe add drunk to this?.. but too much
       hardcoding is annoying.. -- Altrag */
    if ( !str_cmp(cmd->name, "flee") &&
          IS_AFFECTED(ch, AFF_BERSERK) )
    {
        send_to_char( "You aren't thinking very clearly..\n", ch);
        return;
    }

    /*
     * Dispatch the command.
     */
    ch->prev_cmd = ch->last_cmd;    /* haus, for automapping */
    ch->last_cmd = cmd->do_fun;
    start_timer(&time_used);
    call_do_fun_str( cmd->do_fun, ch, arg );
    end_timer(&time_used);
    /*
     * Update the record of how many times this command has been used (haus)
     */
    update_userec(&time_used, &cmd->userec);
    tmptime = UMIN(time_used.tv_sec,19) * 1000000 + time_used.tv_usec;

    /* laggy command notice: command took longer than 1.5 seconds */
    if ( tmptime > 1500000 )
    {
        log_printf_plus(LOG_NORMAL, get_trust(ch), "[*****] LAG: %s: %s %s (R:%d S:%d.%06d)",
                ch->name ? ch->name : "(null)",
                cmd->name, (cmd->log == LOG_NEVER ? "XXX" : arg.c_str() ),
		ch->in_room ? ch->in_room->vnum : 0,
		(int) (time_used.tv_sec),(int) (time_used.tv_usec) );
    }

    tail_chain( ch->game );
}

void interpret( CHAR_DATA *ch, char *argument )
{
    interpret( ch, std::string( argument ? argument : "" ) );
}

CMDTYPE *find_command( const std::string& command )
{
    CMDTYPE *cmd;
    int hash;

    if ( command.empty() )
        return NULL;

    hash = command_hash_bucket(command.front());

    for ( cmd = command_hash[hash]; cmd; cmd = cmd->next )
	if ( !str_prefix( command, cmd->name ) )
	    return cmd;

    return NULL;
}

SOCIALTYPE *find_social( const std::string& command )
{
    SOCIALTYPE *social;
    int hash;

    if ( command.empty() )
        return NULL;

    unsigned char c = static_cast<unsigned char>( LOWER( static_cast<unsigned char>( command[0] ) ) );

    if( c < 'a' || c > 'z' )
        hash = 0;
    else
        hash = ( c - 'a' ) + 1;

    for( social = social_index[hash]; social; social = social->next )
        if( !str_prefix( command, social->name ) )
            return social;

    return NULL;
}

bool check_social( CHAR_DATA *ch, const std::string& command, const std::string& argument )
{
    std::string arg;
    std::string argstr = argument;
    CHAR_DATA *victim;
    SOCIALTYPE *social;

    if ( !ch || !ch->in_room )
        return FALSE;

    if ( (social=find_social(command)) == NULL )
	    return FALSE;

    if ( !IS_NPC(ch) && BV_IS_SET(ch->act, PLR_NO_EMOTE) )
    {
        send_to_char( "You are anti-social!\n", ch );
        return TRUE;
    }

    switch ( ch->position )
    {
        case POS_DEAD:
        send_to_char( "Lie still; you are DEAD.\n", ch );
        return TRUE;

        case POS_INCAP:
        case POS_MORTAL:
        send_to_char( "You are hurt far too bad for that.\n", ch );
        return TRUE;

        case POS_STUNNED:
        send_to_char( "You are too stunned to do that.\n", ch );
        return TRUE;

        case POS_SLEEPING:
        /*
        * I just know this is the path to a 12" 'if' statement.  :(
        * But two players asked for it already!  -- Furey
        */
        if ( !str_cmp( social->name, "snore" ) )
            break;
        send_to_char( "In your dreams, or what?\n", ch );
        return TRUE;

    }

    one_argument( argstr, arg );
    victim = NULL;
    if ( arg.empty() )
    {
        act( AT_SOCIAL, social->others_no_arg, ch, NULL, victim, TO_ROOM    );
        act( AT_SOCIAL, social->char_no_arg,   ch, NULL, victim, TO_CHAR    );
    }
    else if ( ( victim = get_char_room( ch, arg ) ) == NULL )
    {
        send_to_char( "They aren't here.\n", ch );
    }
    else if ( victim == ch )
    {
        act( AT_SOCIAL, social->others_auto,   ch, NULL, victim, TO_ROOM    );
        act( AT_SOCIAL, social->char_auto,     ch, NULL, victim, TO_CHAR    );
    }
    else
    {
        act( AT_SOCIAL, social->others_found,  ch, NULL, victim, TO_NOTVICT );
        act( AT_SOCIAL, social->char_found,    ch, NULL, victim, TO_CHAR    );
        act( AT_SOCIAL, social->vict_found,    ch, NULL, victim, TO_VICT    );

        if ( !IS_NPC(ch) && IS_NPC(victim)
        &&   !IS_AFFECTED(victim, AFF_CHARM)
        &&   IS_AWAKE(victim) 
            &&   !IS_SET( victim->pIndexData->progtypes, ACT_PROG ) )
        {
            switch ( number_bits( 4 ) )
            {
            case 0:
            if ( ch->in_room && !BV_IS_SET(ch->in_room->room_flags, ROOM_SAFE )
            &&    IS_EVIL(ch) )
            {
                if ( !str_cmp( social->name, "slap" ) || !str_cmp( social->name, "punch" ) )
                multi_hit( victim, ch, TYPE_UNDEFINED );
            }
            else
            if ( IS_NEUTRAL(ch) )
            {
                act( AT_ACTION, "$n slaps $N.",  victim, NULL, ch, TO_NOTVICT );
                act( AT_ACTION, "You slap $N.",  victim, NULL, ch, TO_CHAR    );
                act( AT_ACTION, "$n slaps you.", victim, NULL, ch, TO_VICT    );
            }
            else
            {
                act( AT_ACTION, "$n acts like $N doesn't even exist.",  victim, NULL, ch, TO_NOTVICT );
                act( AT_ACTION, "You just ignore $N.",  victim, NULL, ch, TO_CHAR    );
                act( AT_ACTION, "$n appears to be ignoring you.", victim, NULL, ch, TO_VICT    );
            }
            break;

            case 1: case 2: case 3: case 4:
            case 5: case 6: case 7: case 8:
            act( AT_SOCIAL, social->others_found,
                victim, NULL, ch, TO_NOTVICT );
            act( AT_SOCIAL, social->char_found,
                victim, NULL, ch, TO_CHAR    );
            act( AT_SOCIAL, social->vict_found,
                victim, NULL, ch, TO_VICT    );
            break;

            case 9: case 10: case 11: case 12:
            act( AT_ACTION, "$n slaps $N.",  victim, NULL, ch, TO_NOTVICT );
            act( AT_ACTION, "You slap $N.",  victim, NULL, ch, TO_CHAR    );
            act( AT_ACTION, "$n slaps you.", victim, NULL, ch, TO_VICT    );
            break;
            }
        }
    }

    return TRUE;
}



/*
 * Return true if an argument is completely numeric.
 */
bool is_number(char *arg)
{
    if (!arg)
        return FALSE;

    return is_number(std::string(arg));
}
bool is_number(const std::string& arg)
{
    if (arg.empty())
        return FALSE;

    size_t i = 0;
    if (arg[0] == '-' || arg[0] == '+')
        i = 1;
    if (i >= arg.size())
        return FALSE;

    for (; i < arg.size(); ++i)
    {
        if (!isdigit(static_cast<unsigned char>(arg[i])))
            return FALSE;
    }

    return TRUE;
}



/*
 * Given a string like 14.foo, return 14 and 'foo'
 */
int number_argument_old( char *argument, char *arg )
{
    char *pdot;
    int number;

    for ( pdot = argument; *pdot != '\0'; pdot++ )
    {
	if ( *pdot == '.' )
	{
	    *pdot = '\0';
	    number = atoi( argument );
	    *pdot = '.';
	    snprintf( arg, MAX_INPUT_LENGTH, "%s", pdot+1 );
	    return number;
	}
    }

    snprintf( arg, MAX_INPUT_LENGTH, "%s", argument );
    return 1;
}

int number_argument(const std::string& argument, std::string& arg)
{
    std::string::size_type dot = argument.find('.');

    if (dot != std::string::npos)
    {
        std::string number_part = argument.substr(0, dot);
        arg = argument.substr(dot + 1);

        return strtoi(number_part);
    }

    arg = argument;
    return 1;
}

/*
 * Pick off one argument from a string and return the rest.
 * Understands quotes.
 */
static std::pair<std::string, std::string>
one_argument_std(const std::string& input)
{
    const char* p = input.c_str();
    char cEnd = ' ';
    std::string first;

    first.reserve(input.size());

    /* Skip leading spaces */
    while (*p && isspace_utf8(p))
        UTF8_NEXT(p);

    /* Determine delimiter */
    if (*p == '\'' || *p == '"')
        cEnd = *p++;

    /* Copy first argument */
    size_t count = 0;
    while (*p != '\0' && *p != cEnd)
    {
        size_t char_len = utf8_char_len_safe(p);

        /* Respect old MAX_INPUT_LENGTH - 1 truncation behavior */
        if (count + char_len >= MAX_INPUT_LENGTH - 1)
            break;

        if (char_len == 1)
        {
            first.push_back(static_cast<char>(
                tolower(static_cast<unsigned char>(*p))
            ));
        }
        else
        {
            first.append(p, char_len);
        }

        p += char_len;
        count += char_len;
    }

    /* Skip closing quote if present */
    if (*p == cEnd)
        ++p;

    /* Skip trailing spaces */
    while (*p && isspace_utf8(p))
        UTF8_NEXT(p);

    return { first, std::string(p) };
}

std::string one_argument(const std::string& input, std::string& arg_first)
{
    auto result = one_argument_std(input);
    arg_first = std::move(result.first);
    return result.second;
}

/*
 * Pick off one argument from a string and return the rest.
 * Understands quotes.  Delimiters = { ' ', '-' }
 * Rewritten to use std::string - MDM 3-12-26
 */
std::string one_argument2(const std::string& input, std::string& arg_first)
{
    const char* argument = input.c_str();
    char cEnd;
    size_t count = 0;

    arg_first.clear();

    // Skip leading spaces
    while (*argument && isspace_utf8(argument))
        UTF8_NEXT(argument);

    // Determine delimiter
    cEnd = ' ';
    if (*argument == '\'' || *argument == '"')
        cEnd = *argument++;

    // Copy argument safely
    while (*argument != '\0')
    {
        /* ASCII-safe delimiter checks */
        if ((unsigned char)*argument == (unsigned char)cEnd ||
            (unsigned char)*argument == (unsigned char)'-')
            break;

        size_t char_len = utf8_char_len_safe(argument);

        /* Prevent overflow (respect multibyte) */
        if (count + char_len >= MAX_INPUT_LENGTH - 1)
            break;

        if (char_len == 1) /* ASCII */
        {
            arg_first.push_back(
                static_cast<char>(tolower(static_cast<unsigned char>(*argument)))
            );
        }
        else /* UTF-8 multibyte */
        {
            arg_first.append(argument, char_len);
        }

        argument += char_len;
        count += char_len;
    }

    // Skip closing delimiter or '-' if present
    if (*argument == cEnd || *argument == '-')
        argument++;

    // Skip trailing spaces
    while (*argument && isspace_utf8(argument))
        UTF8_NEXT(argument);

    return std::string(argument);
}

int number_argument(char *argument, char *arg)
{
    if (!argument || !arg)
        return 1;

    std::string out;
    int number = number_argument(std::string(argument), out);

    std::strncpy(arg, out.c_str(), MAX_INPUT_LENGTH - 1);
    arg[MAX_INPUT_LENGTH - 1] = '\0';

    return number;
}

void do_timecmd( CHAR_DATA *ch, char *argument )
{
  struct timeval stime;
  struct timeval etime;
  static bool timing;
  extern CHAR_DATA *timechar;
  std::string arg;
  const std::string input = argument ? argument : "";
  
  send_to_char("Timing\n",ch);
  if ( timing )
    return;
  one_argument( input, arg );
  if ( arg.empty() )
  {
    send_to_char( "No command to time.\n", ch );
    return;
  }
  if ( !str_cmp(arg, "update") )
  {
    if ( timechar )
      send_to_char( "Another person is already timing updates.\n", ch );
    else
    {
      timechar = ch;
      send_to_char( "Setting up to record next update loop.\n", ch );
    }
    return;
  }
  set_char_color(AT_PLAIN, ch);
  send_to_char( "Starting timer.\n", ch );
  timing = TRUE;
  gettimeofday(&stime, NULL);
  interpret(ch, input);
  gettimeofday(&etime, NULL);
  timing = FALSE;
  if ( char_died( ch ) )
    return;
  set_char_color(AT_PLAIN, ch);
  send_to_char( "Timing complete.\n", ch );
  subtract_times(&etime, &stime);
  ch_printf( ch, "Timing took %lld.%06lld seconds.\n",
      static_cast<long long>( etime.tv_sec ), static_cast<long long>( etime.tv_usec ) );
  return;
}

void start_timer(struct timeval *stime)
{
  if ( !stime )
  {
    bug( "Start_timer: NULL stime." );
    return;
  }
  gettimeofday(stime, NULL);
  return;
}

time_t end_timer(struct timeval *stime)
{
  struct timeval etime;
  
  /* Mark etime before checking stime, so that we get a better reading.. */
  gettimeofday(&etime, NULL);
  if ( !stime || (!stime->tv_sec && !stime->tv_usec) )
  {
    bug( "End_timer: bad stime." );
    return 0;
  }
  subtract_times(&etime, stime);
  /* stime becomes time used */
  *stime = etime;
  return (etime.tv_sec*1000000)+etime.tv_usec;
}

void send_timer(struct timerset *vtime, CHAR_DATA *ch)
{
  if ( vtime->num_uses == 0 )
    return;
  const long long avg_seconds = vtime->total_time.tv_sec / vtime->num_uses;
  const long long carry = (vtime->total_time.tv_sec % vtime->num_uses) * 1000000LL;
  const long long avg_microseconds = (vtime->total_time.tv_usec + carry) / vtime->num_uses;
  ch_printf(ch, "Has been used %d times this boot.\n", vtime->num_uses);
  ch_printf(ch, "Time (in secs): min %lld.%06lld; avg: %lld.%06lld; max %lld.%06lld\n",
      static_cast<long long>( vtime->min_time.tv_sec ), static_cast<long long>( vtime->min_time.tv_usec ),
      avg_seconds, avg_microseconds,
      static_cast<long long>( vtime->max_time.tv_sec ), static_cast<long long>( vtime->max_time.tv_usec ) );
  return;
}

void update_userec(struct timeval *time_used, struct timerset *userec)
{
  userec->num_uses++;
  if ( !timerisset(&userec->min_time)
  ||    timercmp(time_used, &userec->min_time, <) )
  {
    userec->min_time.tv_sec  = time_used->tv_sec;
    userec->min_time.tv_usec = time_used->tv_usec;
  }
  if ( !timerisset(&userec->max_time)
  ||    timercmp(time_used, &userec->max_time, >) )
  {
    userec->max_time.tv_sec  = time_used->tv_sec;
    userec->max_time.tv_usec = time_used->tv_usec;
  }
  userec->total_time.tv_sec  += time_used->tv_sec;
  userec->total_time.tv_usec += time_used->tv_usec;
  while ( userec->total_time.tv_usec >= 1000000 )
  {
    userec->total_time.tv_sec++;
    userec->total_time.tv_usec -= 1000000;
  }
  return;
}

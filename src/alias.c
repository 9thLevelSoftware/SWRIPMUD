/****************************************************************************
 * [S]imulated [M]edieval [A]dventure multi[U]ser [G]ame      |   \\._.//   *
 * -----------------------------------------------------------|   (0...0)   *
 * SMAUG 1.4 (C) 1994, 1995, 1996, 1998  by Derek Snider      |    ).:.(    *
 * -----------------------------------------------------------|    {o o}    *
 * SMAUG code team: Thoric, Altrag, Blodkai, Narn, Haus,      |   / ' ' \   *
 * Scryn, Rennard, Swordbearer, Gorog, Grishnakh, Nivek,      |~'~.VxvxV.~'~*
 * Tricops and Fireblade                                      |             *
 * ------------------------------------------------------------------------ *
 * Merc 2.1 Diku Mud improvments copyright (C) 1992, 1993 by Michael        *
 * Chastain, Michael Quan, and Mitchell Tse.                                *
 * Original Diku Mud copyright (C) 1990, 1991 by Sebastian Hammer,          *
 * Michael Seifert, Hans Henrik St{rfeldt, Tom Madsen, and Katja Nyboe.     *
 * ------------------------------------------------------------------------ *
 *                        Alias module                                      *
 ****************************************************************************/
/******************************************************
            Desolation of the Dragon MUD II
      (C) 1997, 1998  Jesse DeFer and Heath Leach
 http://dotd.mudservices.com  dotd@dotd.mudservices.com 
 ******************************************************/

#include <sys/types.h>
#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <sys/stat.h>
#include "mud.h"

static ALIAS_DATA *find_alias( CHAR_DATA *ch, const std::string& name )
{
    if ( !ch || !ch->pcdata || name.empty() )
        return NULL;

    for ( ALIAS_DATA *alias = ch->pcdata->first_alias; alias; alias = alias->next )
        if ( alias->name && !str_cmp( name, alias->name ) )
            return alias;

    return NULL;
}

void do_alias( CHAR_DATA *ch, char *argument )
{
    ALIAS_DATA *pal = NULL;
    std::string arg, argstr = argument ? argument : "";
    
    if (!ch || !ch->pcdata)
        return;
    
    argstr = one_argument(argstr, arg);
    smash_tilde( arg );
    smash_tilde( argstr );
    
    if ( arg.empty() )
    {
        if (!ch->pcdata->first_alias)
        {
            send_to_char("You have no aliases defined!\n", ch);
            return;
        }
        pager_printf( ch, "%-20s What it does\n", "Alias" );
        for (pal=ch->pcdata->first_alias;pal;pal=pal->next)
            pager_printf( ch, "%-20s %s\n",
                          pal->name, pal->cmd );
        return;
    }
    
    if ( argstr.empty() )
    {
        if ( (pal = find_alias(ch, arg)) != NULL )
        {
            STR_DISPOSE(pal->name);
            STR_DISPOSE(pal->cmd);
            UNLINK(pal, ch->pcdata->first_alias, ch->pcdata->last_alias, next, prev);
            DISPOSE(pal);
            send_to_char("Deleted Alias.\n", ch);
        } 
	  else
            send_to_char("That alias does not exist.\n", ch);
        return;
    }
    
    if ( (pal=find_alias(ch, arg)) == NULL )
    {
        CREATE(pal, ALIAS_DATA, 1);
        pal->name = str_dup(arg);
        pal->cmd  = str_dup(argstr);
        LINK(pal, ch->pcdata->first_alias, ch->pcdata->last_alias, next, prev);
        send_to_char("Created Alias.\n", ch);
    } 
    else 
    {
        if (pal->cmd)
            STR_DISPOSE(pal->cmd);
        pal->cmd  = str_dup(argstr);
        send_to_char("Modified Alias.\n", ch);
    }
}

void free_aliases( CHAR_DATA *ch )
{
    ALIAS_DATA *pal, *next_pal;
    
    if (!ch || !ch->pcdata)
        return;
    
    for (pal=ch->pcdata->first_alias;pal;pal=next_pal)
    {
        next_pal=pal->next;
        if (pal->name)
            STR_DISPOSE(pal->name);
        if (pal->cmd)
            STR_DISPOSE(pal->cmd);
        DISPOSE( pal );
    }
    ch->pcdata->first_alias = NULL;
    ch->pcdata->last_alias = NULL;
}

bool check_alias( CHAR_DATA *ch, const std::string& command, const std::string& argument )
{
    ALIAS_DATA *alias = find_alias( ch, command );
    if ( !alias || !alias->cmd || !*alias->cmd )
        return FALSE;

    if ( char_died( ch ) || ( ch->desc && ( ch->desc->closing || ch->desc->write_failed
            || ch->desc->character != ch ) ) )
        return TRUE;

    if ( ch->cmd_recurse >= 50 )
    {
        send_to_char( "Unable to further process command, recurses too much.\n", ch );
        return TRUE;
    }

    struct AliasDepth
    {
        CHAR_DATA *character;
        sh_int previous;

        ~AliasDepth()
        {
            if ( !char_died( character ) )
                character->cmd_recurse = previous;
        }
    } depth{ ch, ch->cmd_recurse };

    ++ch->cmd_recurse;
    std::string arg = alias->cmd;
    if ( !argument.empty() )
        arg += " " + argument;

    interpret( ch, arg );
    return TRUE;
}

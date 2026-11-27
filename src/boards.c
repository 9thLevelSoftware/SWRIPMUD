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
*			     Special boards module			   *
****************************************************************************/

#include <sys/types.h>
#include <errno.h>
#include <ctype.h>
#include <limits>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <sys/stat.h>
#include <unistd.h>
#include "mud.h"

/* Defines for voting on notes. -- Narn */
#define VOTE_NONE 0
#define VOTE_OPEN 1
#define VOTE_CLOSED 2

static const int DEFAULT_BOARD_MAX_POSTS = 50;

BOARD_DATA *		first_board;
BOARD_DATA *		last_board;

bool	is_note_to	args( ( CHAR_DATA *ch, NOTE_DATA *pnote ) );
void	note_attach	args( ( CHAR_DATA *ch ) );
void	note_remove	args( ( CHAR_DATA *ch, BOARD_DATA *board,
				NOTE_DATA *pnote ) );
void  	do_note		args( ( CHAR_DATA *ch, const std::string& arg_passed, bool IS_MAIL) );
bool check_parse_name( const std::string& name );



bool can_remove( CHAR_DATA *ch, BOARD_DATA *board )
{
  if ( !ch || !board )
    return FALSE;

  /* If your trust is high enough, you can remove it. */
  if ( get_trust( ch ) >= board->min_remove_level )
    return TRUE;
  
  if ( board->extra_removers && board->extra_removers[0] != '\0' )
  {
    if ( ch->name && is_name( ch->name, board->extra_removers ) )
      return TRUE;
  }
  return FALSE;
}
 
bool can_read( CHAR_DATA *ch, BOARD_DATA *board )
{
  if ( !ch || !board || IS_NPC( ch ) || !ch->pcdata )
    return FALSE;

  /* If your trust is high enough, you can read it. */
  if ( get_trust( ch ) >= board->min_read_level )
    return TRUE;

  /* Your trust wasn't high enough, so check if a read_group or extra
     readers have been set up. */
  if ( board->read_group && board->read_group[0] != '\0' )
  {
    if ( ch->pcdata->clan && ch->pcdata->clan->name && !str_cmp( ch->pcdata->clan->name, board->read_group ) )
      return TRUE; 
    if ( ch->pcdata->clan && ch->pcdata->clan->mainclan && ch->pcdata->clan->mainclan->name && !str_cmp( ch->pcdata->clan->mainclan->name, board->read_group ) )
      return TRUE; 
    
  }
  if ( board->extra_readers && board->extra_readers[0] != '\0' )
  {
    if ( ch->name && is_name( ch->name, board->extra_readers ) )
      return TRUE;
  } 
  return FALSE;
}

bool can_post( CHAR_DATA *ch, BOARD_DATA *board )
{
  if ( !ch || !board || IS_NPC( ch ) || !ch->pcdata )
    return FALSE;

  /* If your trust is high enough, you can post. */
  if ( get_trust( ch ) >= board->min_post_level )
    return TRUE;

  /* Your trust wasn't high enough, so check if a post_group has been set up. */
  if ( board->post_group && board->post_group[0] != '\0' )
  {
    if ( ch->pcdata->clan && ch->pcdata->clan->name && !str_cmp( ch->pcdata->clan->name, board->post_group ) )
      return TRUE; 
    if ( ch->pcdata->clan && ch->pcdata->clan->mainclan && ch->pcdata->clan->mainclan->name && !str_cmp( ch->pcdata->clan->mainclan->name, board->post_group ) )
      return TRUE; 
  }
  return FALSE;
}


/*
 * board commands.
 */
static BOARD_DATA *find_board_by_filename( const std::string &filename )
{
    for ( BOARD_DATA *board = first_board; board; board = board->next )
        if ( !str_cmp( filename, board->note_file ) )
            return board;
    return NULL;
}

static bool valid_board_filename( const std::string &filename )
{
    return filename.size() >= 3 && filename.size() < 256 - sizeof(BOARD_DIR)
        && filename.front() != '.' && filename.back() != '.'
        && str_cmp( filename, BOARD_FILE ) && filename.find( ".." ) == std::string::npos
        && filename.find_first_not_of( "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" ) == std::string::npos;
}

static bool new_board_filename( CHAR_DATA *ch, const std::string &filename )
{
    if ( !valid_board_filename( filename ) )
    {
        send_to_char( "Enter a board filename using letters, digits, dots, hyphens or underscores.\n", ch );
        return false;
    }
    if ( find_board_by_filename( filename ) )
    {
        send_to_char( "A board already uses that filename.\n", ch );
        return false;
    }
    return is_valid_filename( ch, BOARD_DIR, filename );
}

template <typename Write>
static bool write_board_file( const std::string &filename, Write write )
{
    std::string temporary = filename + ".tmp.XXXXXX";
    const int fd = mkstemp( temporary.data() );
    if ( fd < 0 )
    {
        perror( filename.c_str() );
        return false;
    }

    FILE *fp = fdopen( fd, "w" );
    if ( !fp )
    {
        perror( filename.c_str() );
        close( fd );
        unlink( temporary.c_str() );
        return false;
    }

    write( fp );
    const bool written = !ferror( fp ) && fflush( fp ) == 0 && fsync( fd ) == 0;
    const bool closed = fclose( fp ) == 0;
    if ( written && closed && rename( temporary.c_str(), filename.c_str() ) == 0 )
        return true;

    perror( filename.c_str() );
    unlink( temporary.c_str() );
    return false;
}

static bool write_boards_txt( const BOARD_DATA *original = NULL, const BOARD_DATA *replacement = NULL )
{
    return write_board_file( BOARD_DIR BOARD_FILE, [&]( FILE *fpout ) {
        for ( const BOARD_DATA *entry = first_board; entry; entry = entry->next )
        {
            const BOARD_DATA *board = entry == original ? replacement : entry;
            fprintf( fpout, "Filename          %s~\n", board->note_file );
            fprintf( fpout, "Vnum              %d\n",  board->board_obj );
            fprintf( fpout, "Min_read_level    %d\n",  board->min_read_level );
            fprintf( fpout, "Min_post_level    %d\n",  board->min_post_level );
            fprintf( fpout, "Min_remove_level  %d\n",  board->min_remove_level );
            fprintf( fpout, "Max_posts         %d\n",  board->max_posts );
            fprintf( fpout, "Type              %d\n",  board->type );
            fprintf( fpout, "Read_group        %s~\n", board->read_group );
            fprintf( fpout, "Post_group        %s~\n", board->post_group );
            fprintf( fpout, "Extra_readers     %s~\n", board->extra_readers );
            fprintf( fpout, "Extra_removers    %s~\n", board->extra_removers );
            fprintf( fpout, "End\n" );
        }
        fprintf( fpout, "$\n" );
    } );
}

BOARD_DATA *get_board( OBJ_DATA *obj )
{
    BOARD_DATA *board;

    if ( !obj || !obj->pIndexData )
      return NULL;

    for ( board = first_board; board; board = board->next )
       if ( board->board_obj == obj->pIndexData->vnum )
         return board;
    return NULL;	
}

BOARD_DATA *find_board( CHAR_DATA *ch )
{
    OBJ_DATA *obj;
    BOARD_DATA  *board;

    if ( !ch || !ch->in_room )
      return NULL;

    for ( obj = ch->in_room->first_content;
	  obj;
	  obj = obj->next_content )
    {
	if ( (board = get_board(obj)) != NULL )
	    return board;
    }

    return NULL;
}


bool is_note_to( CHAR_DATA *ch, NOTE_DATA *pnote )
{
    if ( !ch || !pnote )
	return FALSE;

    if ( !str_cmp( ch->name, pnote->sender ) )
	return TRUE;

    if ( is_name( "all", pnote->to_list ) )
	return TRUE;

    if ( IS_HERO(ch) && is_name( "immortal", pnote->to_list ) )
	return TRUE;

    if ( is_name( ch->name, pnote->to_list ) )
	return TRUE;

    return FALSE;
}



void note_attach( CHAR_DATA *ch )
{
    NOTE_DATA *pnote;

    if ( !ch || IS_NPC( ch ) || !ch->pcdata )
        return;

    if ( ch->pcdata->pnote )
	return;

    CREATE( pnote, NOTE_DATA, 1 );
    pnote->next		= NULL;
    pnote->prev		= NULL;
    pnote->sender	= QUICKLINK( ch->name );
    pnote->date		= STRALLOC( "" );
    pnote->to_list	= STRALLOC( "" );
    pnote->subject	= STRALLOC( "" );
    pnote->text		= STRALLOC( "" );
    ch->pcdata->pnote	= pnote;
    return;
}

static bool write_board( const BOARD_DATA *board )
{
    return write_board_file( std::string( BOARD_DIR ) + board->note_file, [&]( FILE *fp ) {
	for ( const NOTE_DATA *pnote = board->first_note; pnote; pnote = pnote->next )
	{
	    fprintf( fp, "Sender  %s~\nDate    %s~\nTo      %s~\nSubject %s~\nVoting %d\nYesvotes %s~\nNovotes %s~\nAbstentions %s~\nText\n%s~\n\n",
		pnote->sender,
		pnote->date,
		pnote->to_list,
		pnote->subject,
                pnote->voting,
                pnote->yesvotes,
                pnote->novotes,
                pnote->abstentions,
		pnote->text
		);
	}
    } );
}


void free_note( NOTE_DATA *pnote )
{
    if ( pnote->text )    STRFREE( pnote->text );
    if ( pnote->subject ) STRFREE( pnote->subject );
    if ( pnote->to_list ) STRFREE( pnote->to_list );
    if ( pnote->date )    STRFREE( pnote->date );
    if ( pnote->sender )  STRFREE( pnote->sender );
    if ( pnote->yesvotes )
	STR_DISPOSE( pnote->yesvotes );
    if ( pnote->novotes )
	STR_DISPOSE( pnote->novotes );
    if ( pnote->abstentions )
	STR_DISPOSE( pnote->abstentions );
    DISPOSE( pnote );
}

void note_remove( CHAR_DATA *ch, BOARD_DATA *board, NOTE_DATA *pnote )
{

    if ( !board )
    {
      bug( "%s: null board", __func__ );
      return;
    }

    if ( !pnote )
    {
      bug( "%s: null pnote", __func__ );
      return;
    }
    
    /*
     * Remove note from linked list.
     */
    UNLINK( pnote, board->first_note, board->last_note, next, prev );

    --board->num_posts;
    free_note( pnote );
    write_board( board );
}


OBJ_DATA *find_quill( CHAR_DATA *ch )
{
    OBJ_DATA *quill;

    for ( quill = ch->last_carrying; quill; quill = quill->prev_content )
 	if ( quill->item_type == ITEM_PEN
        &&   can_see_obj( ch, quill ) )
	  return quill;
    return quill;
}

void do_noteroom( CHAR_DATA *ch, char *argument )
{
    BOARD_DATA *board;
    std::string arg;
    char arg_passed[MAX_STRING_LENGTH];
    const char *safe_arg = argument ? argument : "";

    SPRINTF(arg_passed, "%s", safe_arg);

    switch( ch->substate )
    {
	case SUB_WRITING_NOTE:
	do_note(ch, arg_passed, FALSE);
 	break;

	default:

    one_argument(safe_arg, arg);
    smash_tilde( arg_passed );
    if (!str_cmp(arg, "write") || !str_cmp(arg, "to") 
    ||  !str_cmp(arg, "subject") || !str_cmp(arg, "show"))        
    {
        do_note(ch, arg_passed, FALSE);
        return;  
    }
 	    
    board = find_board( ch );
    if ( !board )
    {
        send_to_char( "There is no bulletin board here to look at.\n", ch );
        return;
    }

    if (board->type != BOARD_NOTE)
    {
      send_to_char("You can only use note commands on a message terminal.\n", ch);
      return;
    }
    else
    {
      do_note(ch, arg_passed, FALSE);
      return;
    }
  }
}

void do_mailroom(CHAR_DATA *ch, char *argument)
{
    BOARD_DATA *board;
    std::string arg;
    char arg_passed[MAX_STRING_LENGTH];
    const char *safe_arg = argument ? argument : "";

    SPRINTF(arg_passed, "%s", safe_arg);

    switch( ch->substate )
    {
	case SUB_WRITING_NOTE:
	do_note(ch, arg_passed, TRUE);
 	break;

	default:

    one_argument(safe_arg, arg);
  smash_tilde( arg_passed );
    if (!str_cmp(arg, "write") || !str_cmp(arg, "to") 
    ||  !str_cmp(arg, "subject") || !str_cmp(arg, "show"))        
    {
        do_note(ch, arg_passed, TRUE);
	return;  
    }

    board = find_board( ch );
    if ( !board )
    {
        send_to_char( "There is no mail facility here.\n", ch );
        return;
    }

    if (board->type != BOARD_MAIL)
    {
	send_to_char("You can only use mail commands in a post office.\n", ch);
	return;
    }
    else
    {
	do_note(ch, arg_passed, TRUE);
	return;
    }
  }
}

void do_note( CHAR_DATA *ch, const std::string& arg_passed, bool IS_MAIL )
{
    std::string buf, notebuf, short_desc_buf, long_desc_buf, keyword_buf;
    std::string arg;
	std::string argstr = arg_passed;
    NOTE_DATA  *pnote;
    BOARD_DATA *board;
    int vnum;
    int anum;
    int first_list;
    OBJ_DATA *quill, *paper, *tmpobj = NULL;
    EXTRA_DESCR_DATA *ed = NULL;
    bool mfound = FALSE;
    bool wasfound = FALSE;

    if ( !ch || IS_NPC(ch) || !ch->pcdata )
	return;

    if ( !ch->desc || ch->desc->closing || ch->desc->write_failed )
    {
      	bug( "%s: no descriptor", __func__ );
		return;
    }

    switch( ch->substate )
    {
	default:
	  break;
	case SUB_WRITING_NOTE:
	  if ( ( paper = get_eq_char(ch, WEAR_HOLD) ) == NULL
	  ||     paper->item_type != ITEM_PAPER )
	  {
         bug( "%s: player not holding paper", __func__ );
	     stop_editing( ch );
	     return;
      }
	  if ( !ch->dest_buf )
	  {
	     bug( "%s: NULL ch->dest_buf", __func__ );
	     stop_editing( ch );
	     return;
	  }
	  ed = NULL;
	  for ( EXTRA_DESCR_DATA *ped = paper->first_extradesc; ped; ped = ped->next )
	  {
	     if ( ped == ch->dest_buf )
	     {
	        ed = ped;
	        break;
	     }
	  }
	  if ( !ed )
	  {
	     bug( "%s: ch->dest_buf does not match held paper", __func__ );
	     stop_editing( ch );
	     return;
	  }
	  if ( ed->description )
	     STRFREE( ed->description );
	  ed->description = copy_buffer( ch );
	  stop_editing( ch );	   
	  return;
    }

    set_char_color( AT_NOTE, ch );
    argstr = one_argument( argstr, arg );
    smash_tilde( argstr );

    if ( !str_cmp( arg, "list" ) )
    {
	board = find_board( ch );
	if ( !board )
	{
	    send_to_char( "There is no board here to look at.\n", ch );
	    return;
	}
	if ( !can_read( ch, board ) )
	{
	    send_to_char( "You cannot make any sense of the cryptic scrawl on this board...\n", ch );
	    return;
	}

        first_list = strtoi(argstr);
        if (first_list)
        {
	    if (IS_MAIL)
            {
		send_to_char( "You cannot use a list number (at this time) with mail.\n", ch);
		return;
 	    }
	  
	    if (first_list < 1)
	    {
		send_to_char( "You can't read a message before 1!\n", ch);
		return;
	    }
	}
        

        if (!IS_MAIL)
        {
	    vnum = 0;
	    set_pager_color( AT_NOTE, ch );
	    for ( pnote = board->first_note; pnote; pnote = pnote->next )
            {
		vnum++;
		if ( (first_list && vnum >= first_list) || !first_list )
		   pager_printf( ch, "%2d%c %-12s%c %-12s %s\n",
			vnum,
			is_note_to( ch, pnote ) ? ')' : '}',
			pnote->sender,
			(pnote->voting != VOTE_NONE) ? (pnote->voting == VOTE_OPEN ? 'V' : 'C') : ':',
			pnote->to_list,
			pnote->subject );
	    }
	    act( AT_ACTION, "$n glances over the messages.", ch, NULL, NULL, TO_ROOM );
	    return;
	}
	else
	{
      	    vnum = 0;


            if (IS_MAIL) /* SB Mail check for Brit */
             {
              for ( pnote = board->first_note; pnote; pnote = pnote->next )
                if (is_note_to( ch, pnote )) mfound = TRUE;

              if ( !mfound && get_trust(ch) < ch->game->get_sysdata()->read_all_mail ) 
               {
                ch_printf( ch, "You have no mail.\n");
                return;
               }
             }

            for ( pnote = board->first_note; pnote; pnote = pnote->next )
		if (is_note_to( ch, pnote ) || get_trust(ch) > ch->game->get_sysdata()->read_all_mail)
          	    ch_printf( ch, "%2d%c %s: %s\n",
			++vnum,
			is_note_to( ch, pnote ) ? '-' : '}',
			pnote->sender,
			pnote->subject );
	    return;
	}
    }

    if ( !str_cmp( arg, "read" ) )
    {
	bool fAll;

	board = find_board( ch );
	if ( !board )
	{
	    send_to_char( "There is no board here to look at.\n", ch );
	    return;
	}
	if ( !can_read( ch, board ) ) 
	{
	    send_to_char( "You cannot make any sense of the cryptic scrawl on this board...\n", ch );
	    return;
	}

	if ( !str_cmp( argstr, "all" ) )
	{
	    fAll = TRUE;
	    anum = 0;
	}
	else
	if ( is_number( argstr ) )
	{
	    fAll = FALSE;
	    anum = strtoi( argstr );
	}
	else
	{
	    send_to_char( "Note read which number?\n", ch );
	    return;
	}

	set_pager_color( AT_NOTE, ch );
	if (!IS_MAIL)
 	{
	    vnum = 0;
	    for ( pnote = board->first_note; pnote; pnote = pnote->next )
            {
		vnum++;
		if ( vnum == anum || fAll )   
		{
		  wasfound = TRUE;
		    pager_printf( ch, "[%3d] %s: %s\n%s\nTo: %s\n%s",
			vnum,
			pnote->sender,
			pnote->subject,
			pnote->date,
			pnote->to_list,
			pnote->text );

		    if ( pnote->yesvotes[0] != '\0' || pnote->novotes[0] != '\0'
                    || pnote->abstentions[0] != '\0' )
		    {
			send_to_pager( "------------------------------------------------------------\n", ch );
			pager_printf( ch, "Votes:\nYes:     %s\nNo:      %s\nAbstain: %s\n",
				pnote->yesvotes, pnote->novotes, pnote->abstentions );
		    }
		    act( AT_ACTION, "$n reads a message.", ch, NULL, NULL, TO_ROOM );
                }   
	    }
	    if ( !wasfound )
	      ch_printf( ch, "No such message: %d\n", anum);
	    return;
	}
	else
	{
	    vnum = 0;
	    for ( pnote = board->first_note; pnote; pnote = pnote->next )
	    {
		if (is_note_to(ch, pnote) || get_trust(ch) > ch->game->get_sysdata()->read_all_mail)
		{
		    vnum++;
		    if ( vnum == anum || fAll )
		    { 
		        wasfound = TRUE;
			if ( ch->gold < 10
			&&   get_trust(ch) < ch->game->get_sysdata()->read_mail_free )
			{
			    send_to_char("It costs 10 credits to read a message.\n", ch);
			    return;
			}
			if (get_trust(ch) < ch->game->get_sysdata()->read_mail_free)
			   ch->gold -= 10;
			pager_printf( ch, "[%3d] %s: %s\n%s\nTo: %s\n%s",
			    vnum,
			    pnote->sender,
			    pnote->subject,
			    pnote->date,
			    pnote->to_list,
			    pnote->text );
		    }
		}   
	    }
	    if (!wasfound)	
	      ch_printf( ch, "No such message: %d\n", anum);
	    return;
	}
    }

    /* Voting added by Narn, June '96 */
    if ( !str_cmp( arg, "vote" ) )
    {
	std::string arg2;
	argstr = one_argument( argstr, arg2 ); 

	board = find_board( ch );
	if ( !board )
	{
	    send_to_char( "There is no bulletin board here.\n", ch );
	    return;
	}
	if ( !can_read( ch, board ) ) 
	{
            send_to_char( "You cannot vote on this board.\n", ch );
            return;
	}

	if ( is_number( arg2 ) )
	    anum = strtoi( arg2 );
	else
	{
            send_to_char( "Note vote which number?\n", ch );
            return;
	}

        if ( str_cmp( argstr, "open" ) && str_cmp( argstr, "close" )
          && str_cmp( argstr, "yes" ) && str_cmp( argstr, "no" ) && str_cmp( argstr, "abstain" ) )
        {
            ch_printf( ch, "Usage: %s vote <number> <open|close|yes|no|abstain>\n",
                       IS_MAIL ? "mail" : "note" );
            return;
        }

	vnum = 1;
	for ( pnote = board->first_note; pnote && vnum < anum; pnote = pnote->next )
	    vnum++;
	if ( !pnote )
	{
	    send_to_char( "No such note.\n", ch );
	    return;
	}

	/* Options: open close yes no abstain */
	/* If you're the author of the note and can read the board you can open 
	   and close voting, if you can read it and voting is open you can vote.
	*/
	if ( !str_cmp( argstr, "open" ) )
	{
	    if ( str_cmp( ch->name, pnote->sender ) )
	    {
		send_to_char( "You are not the author of this message.\n", ch );
		return;
	    }
	    pnote->voting = VOTE_OPEN;
	    act( AT_ACTION, "$n opens voting on a note.", ch, NULL, NULL, TO_ROOM );
            send_to_char( "Voting opened.\n", ch );
            write_board( board );
            return;
	}  
	if ( !str_cmp( argstr, "close" ) )
	{
	    if ( str_cmp( ch->name, pnote->sender ) )
	    {
		send_to_char( "You are not the author of this message.\n", ch );
		return;
	    }
	    pnote->voting = VOTE_CLOSED;
	    act( AT_ACTION, "$n closes voting on a note.", ch, NULL, NULL, TO_ROOM );
	    send_to_char( "Voting closed.\n", ch );
	    write_board( board );
	    return;
	}  

	/* Make sure the note is open for voting before going on. */
	if ( pnote->voting != VOTE_OPEN )
	{
	    send_to_char( "Voting is not open on this note.\n", ch );
	    return;
	}

	/* Can only vote once on a note. */
	buf = str_printf("%s %s %s", 
		pnote->yesvotes, pnote->novotes, pnote->abstentions );
	if ( is_name( ch->name, buf ) )
	{
	    send_to_char( "You have already voted on this note.\n", ch );
	    return;
	}
	if ( !str_cmp( argstr, "yes" ) )
	{
	    buf = str_printf("%s %s", pnote->yesvotes, ch->name);
	    STR_DISPOSE( pnote->yesvotes );
	    pnote->yesvotes = str_dup( buf );
	    act( AT_ACTION, "$n votes on a note.", ch, NULL, NULL, TO_ROOM );
	    send_to_char( "Ok.\n", ch );
	    write_board( board );
	    return;
	}  
	if ( !str_cmp( argstr, "no" ) )
	{
	    buf = str_printf("%s %s", pnote->novotes, ch->name );
	    STR_DISPOSE( pnote->novotes );
	    pnote->novotes = str_dup( buf );
	    act( AT_ACTION, "$n votes on a note.", ch, NULL, NULL, TO_ROOM );
	    send_to_char( "Ok.\n", ch );
	    write_board( board );
	    return;
	}  
	if ( !str_cmp( argstr, "abstain" ) )
	{
	    buf = str_printf("%s %s", pnote->abstentions, ch->name );
	    STR_DISPOSE( pnote->abstentions );
	    pnote->abstentions = str_dup( buf );
	    act( AT_ACTION, "$n votes on a note.", ch, NULL, NULL, TO_ROOM );
	    send_to_char( "Ok.\n", ch );
	    write_board( board );
	    return;
	}  
        return;
    }
    if ( !str_cmp( arg, "write" ) )
    {
	if ( ch->substate == SUB_RESTRICTED )
	{
	    send_to_char( "You cannot write a note from within another command.\n", ch );
	    return;
	}
	if (get_trust (ch) < ch->game->get_sysdata()->write_mail_free)
	{
	    quill = find_quill( ch );
            if (!quill)
	    {
		send_to_char("You need a datapad to record a message.\n", ch);
		return;
	    }
	    if ( quill->value[0] < 1 )
	    {
		send_to_char("Your quill is dry.\n", ch);
		return;
	    }
	}
	if ( ( paper = get_eq_char(ch, WEAR_HOLD) ) == NULL
	||     paper->item_type != ITEM_PAPER )
	{
	    if (get_trust(ch) < ch->game->get_sysdata()->write_mail_free )
	    {
		send_to_char("You need to be holding a message disk to write a note.\n", ch);
		return;
	    }
	    OBJ_INDEX_DATA *note_idx = get_obj_index(OBJ_VNUM_NOTE);
	    if ( !note_idx )
	    {
		bug( "%s: missing OBJ_VNUM_NOTE (%d)", __func__, OBJ_VNUM_NOTE );
		send_to_char( "Note disks are currently unavailable.\n", ch );
		return;
	    }
	    paper = create_object( note_idx, 0 );
	    if ((tmpobj = get_eq_char(ch, WEAR_HOLD)) != NULL)
	      unequip_char(ch, tmpobj); 
	    paper = obj_to_char(paper, ch);
	    equip_char(ch, paper, WEAR_HOLD);
	    act(AT_MAGIC, "$n grabs a message tisk to record a note.",
      	        ch, NULL, NULL, TO_ROOM);
  	    act(AT_MAGIC, "You get a message disk to record your note.",
    	        ch, NULL, NULL, TO_CHAR);
	}
	if (paper->value[0] < 2 )
	{
	    paper->value[0] = 1;
	    ed = SetOExtra(paper, "_text_");
	    ch->substate = SUB_WRITING_NOTE;
	    ch->dest_buf = ed;
	    if ( get_trust(ch) < ch->game->get_sysdata()->write_mail_free )
		--quill->value[0];
	    start_editing( ch, ed->description );
	    return;
	}
	else
	{
	    send_to_char("You cannot modify this message.\n", ch);
	    return;
	}
    }

    if ( !str_cmp( arg, "subject" ) )
    {
	if(get_trust(ch) < ch->game->get_sysdata()->write_mail_free)
	{
	    quill = find_quill( ch );
	    if ( !quill )
	    {
		send_to_char("You need a datapad to record a disk.\n", ch);
		return;
	    }
	    if ( quill->value[0] < 1 )
	    {
		send_to_char("Your quill is dry.\n", ch);
		return;
	    }
	}
	if (argstr.empty())
	{
	    send_to_char("What do you wish the subject to be?\n", ch);
	    return;
	}
	if ( ( paper = get_eq_char(ch, WEAR_HOLD) ) == NULL
	||     paper->item_type != ITEM_PAPER )
	{
	    if(get_trust(ch) < ch->game->get_sysdata()->write_mail_free )
	    {
		send_to_char("You need to be holding a message disk to record a note.\n", ch);
		return;
	    }
	    OBJ_INDEX_DATA *note_idx = get_obj_index(OBJ_VNUM_NOTE);
	    if ( !note_idx )
	    {
		bug( "%s: missing OBJ_VNUM_NOTE (%d)", __func__, OBJ_VNUM_NOTE );
		send_to_char( "Note disks are currently unavailable.\n", ch );
		return;
	    }
	    paper = create_object( note_idx, 0 );
    	    if ((tmpobj = get_eq_char(ch, WEAR_HOLD)) != NULL)
		unequip_char(ch, tmpobj); 
	    paper = obj_to_char(paper, ch);
	    equip_char(ch, paper, WEAR_HOLD);
	    act(AT_MAGIC, "$n grabs a message disk.",
		ch, NULL, NULL, TO_ROOM);
	    act(AT_MAGIC, "You get a message disk to record your note.",
		ch, NULL, NULL, TO_CHAR);
	}
	if (paper->value[1] > 1 )
        {
	    send_to_char("You cannot modify this message.\n", ch);
	    return;
        }
        else
	{
	    paper->value[1] = 1;
	    ed = SetOExtra(paper, "_subject_");
	    STRFREE( ed->description );
	    ed->description = STRALLOC( argstr );
	    send_to_char("Ok.\n", ch);
	    return;
	}
    }

    if ( !str_cmp( arg, "to" ) )
    {
	if(get_trust(ch) < ch->game->get_sysdata()->write_mail_free )
	{
	    quill = find_quill( ch );
	    if ( !quill )
	    {
		send_to_char("You need a datapad to record a message.\n", ch);
		return;
	    }
	    if ( quill->value[0] < 1 )
	    {
		send_to_char("Your quill is dry.\n", ch);
		return;
	    }
	}
	if (argstr.empty())
	{
	    send_to_char("Please specify an addressee.\n", ch);
	    return;
	}
	if ( IS_MAIL && str_cmp( argstr, "all" ) )
	{
	    if ( argstr.find_first_not_of( "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ" ) != std::string::npos
	         || !check_parse_name( argstr ) )
	    {
		send_to_char( "No player exists by that name.\n", ch );
		return;
	    }
	    const std::string filename = std::string( PLAYER_DIR ) + static_cast<char>( LOWER(argstr[0]) )
	        + "/" + capitalize( argstr );
	    struct stat recipient;
	    if ( stat( filename.c_str(), &recipient ) != 0 || !S_ISREG( recipient.st_mode ) )
	    {
		send_to_char( "No player exists by that name.\n", ch );
		return;
	    }
	}
	if ( ( paper = get_eq_char(ch, WEAR_HOLD) ) == NULL
	||     paper->item_type != ITEM_PAPER )
	{
	    if(get_trust(ch) < ch->game->get_sysdata()->write_mail_free )
	    {
		send_to_char("You need to be holding a message disk to record a note.\n", ch);
		return;
	    }
	    OBJ_INDEX_DATA *note_idx = get_obj_index(OBJ_VNUM_NOTE);
	    if ( !note_idx )
	    {
		bug( "%s: missing OBJ_VNUM_NOTE (%d)", __func__, OBJ_VNUM_NOTE );
		send_to_char( "Note disks are currently unavailable.\n", ch );
		return;
	    }
	    paper = create_object( note_idx, 0 );
    	    if ((tmpobj = get_eq_char(ch, WEAR_HOLD)) != NULL)
		unequip_char(ch, tmpobj);
	    paper = obj_to_char(paper, ch);
	    equip_char(ch, paper, WEAR_HOLD);
	    act(AT_MAGIC, "$n gets a message disk to record a note.",
	      ch, NULL, NULL, TO_ROOM);
	    act(AT_MAGIC, "You grab a message disk to record your note.",
      	      ch, NULL, NULL, TO_CHAR);
	}

	if (paper->value[2] > 1)
	{
	    send_to_char("You cannot modify this message.\n",ch);
	    return;
	}

	argstr[0] = UPPER(argstr[0]);
	paper->value[2] = 1;
	ed = SetOExtra( paper, "_to_" );
	STRFREE( ed->description );
	ed->description = STRALLOC( argstr );
	send_to_char( "Ok.\n", ch );
	return;
    }

    if ( !str_cmp( arg, "show" ) )
    {
		char *subject, *to_list, *text;

		if ( ( paper = get_eq_char(ch, WEAR_HOLD) ) == NULL
		||     paper->item_type != ITEM_PAPER )
		{
			send_to_char("You are not holding a message disk.\n", ch);
			return;
		}

		if ( (subject = get_extra_descr( "_subject_", paper->first_extradesc )) == NULL )
		subject = "(no subject)";
		if ( (to_list = get_extra_descr( "_to_", paper->first_extradesc )) == NULL )
		to_list = "(nobody)";
		buf = str_printf("%s: %s\nTo: %s\n",
			ch->name,
			subject,
			to_list );
		send_to_char( buf, ch );
		if ( (text = get_extra_descr( "_text_", paper->first_extradesc )) == NULL )
		text = "The disk is blank.\n";
		send_to_char( text, ch );
		return;
    }

    if ( !str_cmp( arg, "post" ) )
    {
		char *strtime, *text; // Could be NULL, which makes it easier for char* then std::string here.

		if ( ( paper = get_eq_char(ch, WEAR_HOLD) ) == NULL
		||     paper->item_type != ITEM_PAPER )
		{
			send_to_char("You are not holding a message disk.\n", ch);
			return;
		}
		
		if ( paper->value[0] == 0 )
		{
			send_to_char("There is nothing written on this disk.\n", ch);
			return;
		}

		if ( paper->value[1] == 0 )
		{
			send_to_char("This message has no subject... using 'none'.\n", ch);
			paper->value[1] = 1;
			ed = SetOExtra(paper, "_subject_");
			STRFREE( ed->description );
			ed->description = STRALLOC( "none" );
		}

		if (paper->value[2] == 0)
		{
		if ( IS_MAIL )
		{
			send_to_char("This message is addressed to no one!\n", ch); 
			return;
		}
		else
		{
			send_to_char("This message is addressed to no one... sending to 'all'!\n", ch);
			paper->value[2] = 1;
			ed = SetOExtra(paper, "_to_");
			STRFREE( ed->description );
			ed->description = STRALLOC( "All" );
		}
		}

		board = find_board( ch );
		if ( !board )
		{
			send_to_char( "There is no terminal here to upload your message to.\n", ch );
			return;
		}
		if ( !can_post( ch, board ) ) 
		{
			send_to_char( "You cannot use this terminal. It is encrypted...\n", ch );
			return;
		}

		if ( board->num_posts >= board->max_posts )
		{
			send_to_char( "This terminal is full. There is no room for your message.\n", ch );
			return;
		}

		strtime				= ctime( &current_time );
		strtime[strlen(strtime)-1]	= '\0';
		CREATE( pnote, NOTE_DATA, 1 );
		pnote->date			= STRALLOC( strtime );

		text = get_extra_descr( "_text_", paper->first_extradesc );
		pnote->text = text ? STRALLOC( text ) : STRALLOC( "" );
		text = get_extra_descr( "_to_", paper->first_extradesc );
		pnote->to_list = text ? STRALLOC( text ) : STRALLOC( "all" );
		text = get_extra_descr( "_subject_", paper->first_extradesc );
		pnote->subject = text ? STRALLOC( text ) : STRALLOC( "" );
		pnote->sender  = QUICKLINK( ch->name );
			pnote->voting      = 0;
			pnote->yesvotes    = str_dup( "" );
			pnote->novotes     = str_dup( "" );
			pnote->abstentions = str_dup( "" );

		LINK( pnote, board->first_note, board->last_note, next, prev );
		board->num_posts++;
		if ( !write_board( board ) )
		{
		    UNLINK( pnote, board->first_note, board->last_note, next, prev );
		    --board->num_posts;
		    free_note( pnote );
		    send_to_char( "Could not save your message.\n", ch );
		    return;
		}
		act( AT_ACTION, "$n uploads a message.", ch, NULL, NULL, TO_ROOM );
		send_to_char( "You upload your message to the terminal.\n", ch );
		extract_obj( paper );
		return;
    }

    if ( !str_cmp( arg, "remove" )
    ||   !str_cmp( arg, "take" )
    ||   !str_cmp( arg, "copy" ) )
    {
	char take;

	board = find_board( ch );
	if ( !board )
	{
	    send_to_char( "There is no terminal here to download a note from!\n", ch );
	    return;
	}
	if ( !str_cmp( arg, "take" ) )
	  take = 1;
	else if ( !str_cmp( arg, "copy" ) )
	{
	  if ( !IS_IMMORTAL(ch) )
	  {
	    send_to_char( "Huh?  Type 'help note' for usage.\n", ch );
	    return;
	  }
	  take = 2;
	}
	else
	  take = 0;

	if ( !is_number( argstr ) )
	{
	    send_to_char( "Note remove which number?\n", ch );
	    return;
	}

	if ( !can_read( ch, board ) ) 
	{
	    send_to_char( "You can't make any sense of what's posted here, let alone remove anything!\n", ch );
	    return;
	}

	anum = strtoi( argstr );
	vnum = 0;
	for ( pnote = board->first_note; pnote; pnote = pnote->next )
	{
	    if (IS_MAIL && ((is_note_to(ch, pnote)) 
	    ||  get_trust(ch) >= ch->game->get_sysdata()->take_others_mail))
	       vnum++;
	    else if (!IS_MAIL)
               vnum++;
	    if ( ( is_note_to( ch, pnote )
	    ||	    can_remove (ch, board)) 
	    &&   ( vnum == anum ) )
	    {
		if ( (is_name("all", pnote->to_list))
		&&   (get_trust( ch ) < ch->game->get_sysdata()->take_others_mail)
		&&   (take != 2) )
		{
		    send_to_char("Notes addressed to 'all' can not be taken.\n", ch);
		    return;
		}

 		if ( take != 0 )
		{
		    OBJ_INDEX_DATA *note_idx = get_obj_index(OBJ_VNUM_NOTE);
		    if ( !note_idx )
		    {
			bug( "%s: missing OBJ_VNUM_NOTE (%d)", __func__, OBJ_VNUM_NOTE );
			send_to_char( "Note disks are currently unavailable.\n", ch );
			return;
		    }
		    if ( ch->gold < 50 && get_trust(ch) < ch->game->get_sysdata()->read_mail_free )
		    {
			if ( take == 1 )
			  send_to_char("It costs 50 credits to take your mail.\n", ch);
			else
			  send_to_char("It costs 50 credits to copy your mail.\n", ch);
			return;
		    }
		    if ( get_trust(ch) < ch->game->get_sysdata()->read_mail_free )
		      ch->gold -= 50;
		    paper = create_object( note_idx, 0 );
		    ed = SetOExtra( paper, "_sender_" );
		    STRFREE( ed->description );
		    ed->description = QUICKLINK(pnote->sender);
		    ed = SetOExtra( paper, "_text_" );
		    STRFREE( ed->description );
		    ed->description = QUICKLINK(pnote->text);
		    ed = SetOExtra( paper, "_to_" );
		    STRFREE( ed->description );
		    ed->description = QUICKLINK( pnote->to_list );
		    ed = SetOExtra( paper, "_subject_" );
		    STRFREE( ed->description );
		    ed->description = QUICKLINK( pnote->subject );
		    ed = SetOExtra( paper, "_date_" );
		    STRFREE( ed->description );
		    ed->description = QUICKLINK( pnote->date );
		    ed = SetOExtra( paper, "note" );
		    STRFREE( ed->description );
		    notebuf = "From: ";
		    notebuf += pnote->sender;		 
		    notebuf += "\nTo: ";
		    notebuf += pnote->to_list;
		    notebuf += "\nSubject: ";
		    notebuf += pnote->subject;
		    notebuf += "\n\n";
		    notebuf += pnote->text;
		    notebuf += "\n";
		    ed->description = STRALLOC(notebuf);
		    paper->value[0] = 2;
		    paper->value[1] = 2;
	 	    paper->value[2] = 2;
		    short_desc_buf = str_printf("a note from %s to %s",
			pnote->sender, pnote->to_list);
		    STRFREE(paper->short_descr);
		    paper->short_descr = STRALLOC(short_desc_buf);
		    long_desc_buf = str_printf("A note from %s to %s lies on the ground.",
			pnote->sender, pnote->to_list);
		    STRFREE(paper->description);
		    paper->description = STRALLOC(long_desc_buf);
		    keyword_buf = str_printf("note parchment paper %s", 
			pnote->to_list);
		    STRFREE(paper->name);
		    paper->name = STRALLOC(keyword_buf);
		}
		if ( take != 2 )
		    note_remove( ch, board, pnote );
		send_to_char( "Ok.\n", ch );
		if ( take == 1 )
		{
		    act( AT_ACTION, "$n downloads a message.", ch, NULL, NULL, TO_ROOM );
		    obj_to_char(paper, ch);
		}
		else if ( take == 2 )
		{
		    act( AT_ACTION, "$n copies a message.", ch, NULL, NULL, TO_ROOM );
		    obj_to_char(paper, ch);
		}
		else
		    act( AT_ACTION, "$n removes a message.", ch, NULL, NULL, TO_ROOM );
		return;
	    }
	}

	send_to_char( "No such message.\n", ch );
	return;
    }

    send_to_char( "Huh?  Type 'help note' for usage.\n", ch );
    return;
}



static int peek_board_char( FILE *fp )
{
    int letter;
    do
    {
        letter = getc( fp );
    }
    while ( letter != EOF && isspace( static_cast<unsigned char>(letter) ) );

    if ( letter != EOF )
        ungetc( letter, fp );
    return letter;
}

template <typename Integer>
static bool read_board_number( FILE *fp, Integer &field )
{
    const char *word = fread_word( fp );
    char *end;
    errno = 0;
    const long number = strtol( word, &end, 10 );
    if ( end == word || *end || errno == ERANGE || number < std::numeric_limits<Integer>::min()
         || number > std::numeric_limits<Integer>::max() )
        return false;
    field = static_cast<Integer>( number );
    return true;
}

static void free_board( BOARD_DATA *board )
{
    if ( board->note_file )     STR_DISPOSE( board->note_file );
    if ( board->read_group )    STR_DISPOSE( board->read_group );
    if ( board->post_group )    STR_DISPOSE( board->post_group );
    if ( board->extra_readers ) STR_DISPOSE( board->extra_readers );
    if ( board->extra_removers ) STR_DISPOSE( board->extra_removers );
    DISPOSE( board );
}

static BOARD_DATA *read_board( GameContext *game, const char *boardfile, FILE *fp )
{
    int letter = peek_board_char( fp );
    if ( letter == EOF || letter == '$' )
        return NULL;

    BOARD_DATA *board;
    CREATE( board, BOARD_DATA, 1 );
    board->game = game;
    board->max_posts = DEFAULT_BOARD_MAX_POSTS;
    while ( letter != EOF && letter != '$' )
    {
        const char *word = fread_word( fp );
        if ( !str_cmp( word, "End" ) )
        {
            if ( !board->read_group )    board->read_group = str_dup( "" );
            if ( !board->post_group )    board->post_group = str_dup( "" );
            if ( !board->extra_readers ) board->extra_readers = str_dup( "" );
            if ( !board->extra_removers ) board->extra_removers = str_dup( "" );
            return board;
        }

        char **field = NULL;
        bool valid_number = true;
        if ( !str_cmp( word, "Filename" ) )              field = &board->note_file;
        else if ( !str_cmp( word, "Read_group" ) )       field = &board->read_group;
        else if ( !str_cmp( word, "Post_group" ) )       field = &board->post_group;
        else if ( !str_cmp( word, "Extra_readers" ) )    field = &board->extra_readers;
        else if ( !str_cmp( word, "Extra_removers" ) )   field = &board->extra_removers;
        else if ( !str_cmp( word, "Vnum" ) )             valid_number = read_board_number( fp, board->board_obj );
        else if ( !str_cmp( word, "Min_read_level" ) )   valid_number = read_board_number( fp, board->min_read_level );
        else if ( !str_cmp( word, "Min_post_level" ) )   valid_number = read_board_number( fp, board->min_post_level );
        else if ( !str_cmp( word, "Min_remove_level" ) ) valid_number = read_board_number( fp, board->min_remove_level );
        else if ( !str_cmp( word, "Max_posts" ) )        valid_number = read_board_number( fp, board->max_posts );
        else if ( !str_cmp( word, "Type" ) )             valid_number = read_board_number( fp, board->type );
        else
        {
            if ( word[0] != '*' )
                bug( "%s: unknown field %s in %s", __func__, word, boardfile );
            fread_to_eol( fp );
        }

        if ( field )
        {
            if ( *field ) STR_DISPOSE( *field );
            *field = fread_string_nohash( fp );
        }
        if ( !valid_number )
            break;
        letter = peek_board_char( fp );
    }

    bug( "%s: incomplete or invalid board record in %s", __func__, boardfile );
    free_board( board );
    return NULL;
}

static NOTE_DATA *read_note( const std::string& notefile, FILE *fp )
{
    NOTE_DATA *pnote;
    char *word;

    if ( peek_board_char( fp ) == EOF )
        return NULL;

    CREATE( pnote, NOTE_DATA, 1 );
    for ( ; ; )
    {
	if ( str_cmp( fread_word( fp ), "sender" ) )
	    break;
	pnote->sender	= fread_string( fp );

	if ( str_cmp( fread_word( fp ), "date" ) )
	    break;
	pnote->date	= fread_string( fp );

	if ( str_cmp( fread_word( fp ), "to" ) )
	    break;
	pnote->to_list	= fread_string( fp );

	if ( str_cmp( fread_word( fp ), "subject" ) )
	    break;
	pnote->subject	= fread_string( fp );

        word = fread_word( fp );
        if ( !str_cmp( word, "voting" ) )
        {
          if ( !read_board_number( fp, pnote->voting ) )
              break;

	  if ( str_cmp( fread_word( fp ), "yesvotes" ) )
	    break;
	  pnote->yesvotes	= fread_string_nohash( fp );

	  if ( str_cmp( fread_word( fp ), "novotes" ) )
	    break;
	  pnote->novotes	= fread_string_nohash( fp );

	  if ( str_cmp( fread_word( fp ), "abstentions" ) )
	    break;
	  pnote->abstentions	= fread_string_nohash( fp );

          word = fread_word( fp );
        }

	if ( str_cmp( word, "text" ) )
	    break;
	pnote->text	= fread_string( fp );
	if ( feof( fp ) || ferror( fp ) )
	    break;

        if ( !pnote->yesvotes )    pnote->yesvotes	= str_dup( "" );
        if ( !pnote->novotes )     pnote->novotes	= str_dup( "" );
        if ( !pnote->abstentions ) pnote->abstentions	= str_dup( "" );
	pnote->next		= NULL;
	pnote->prev		= NULL;
	return pnote;
    }

    bug( "%s: incomplete or invalid note record in %s", __func__, notefile.c_str() );
    free_note( pnote );
    return NULL;
}

/*
 * Load boards file.
 */
void load_boards( GameContext *game )
{
    first_board	= NULL;
    last_board	= NULL;

    const char *boardfile = BOARD_DIR BOARD_FILE;
    FILE *board_fp = fopen( boardfile, "r" );
    if ( !board_fp )
        return;

    BOARD_DATA *board;
    while ( (board = read_board( game, boardfile, board_fp )) != NULL )
    {
        if ( !board->note_file || !valid_board_filename( board->note_file )
             || find_board_by_filename( board->note_file ) )
        {
            bug( "%s: skipping a board with a missing, invalid or duplicate filename in %s", __func__, boardfile );
            free_board( board );
            continue;
        }

        LINK( board, first_board, last_board, next, prev );
        const std::string notefile = std::string( BOARD_DIR ) + board->note_file;
        FILE *note_fp = fopen( notefile.c_str(), "r" );
        if ( !note_fp )
            continue;

        NOTE_DATA *pnote;
        while ( (pnote = read_note( notefile, note_fp )) != NULL )
        {
            LINK( pnote, board->first_note, board->last_note, next, prev );
            board->num_posts++;
        }
        FCLOSE( note_fp );
    }
    FCLOSE( board_fp );
}


void do_makeboard( CHAR_DATA *ch, char *argument )
{
    if ( !argument || argument[0] == '\0' )
    {
	send_to_char( "Usage: makeboard <filename>\n", ch );
	return;
    }

    const std::string filename = strlower( argument );
    if ( !new_board_filename( ch, filename ) )
        return;

    BOARD_DATA *board;
    CREATE( board, BOARD_DATA, 1 );
    board->game = ch->game;
    board->note_file       = str_dup( filename );
    board->max_posts       = DEFAULT_BOARD_MAX_POSTS;
    board->read_group      = str_dup( "" );
    board->post_group      = str_dup( "" );
    board->extra_readers   = str_dup( "" );
    board->extra_removers  = str_dup( "" );
    LINK( board, first_board, last_board, next, prev );
    if ( !write_boards_txt() )
    {
        UNLINK( board, first_board, last_board, next, prev );
        free_board( board );
        send_to_char( "Could not save the board list.\n", ch );
        return;
    }
    send_to_char( "Board created.\n", ch );
}

void do_bset( CHAR_DATA *ch, char *argument )
{
    std::string filename, field;
    std::string value_arg = argument ? argument : "";
    value_arg = one_argument( value_arg, filename );
    value_arg = one_argument( value_arg, field );

    if ( filename.empty() || field.empty() )
    {
        send_to_char( "Usage: bset <board filename> <field> value\n", ch );
        send_to_char( "\nField being one of:\n", ch );
        send_to_char( "  vnum read post remove maxpost filename type\n", ch );
        send_to_char( "  read_group post_group extra_readers extra_removers\n", ch );
        return;
    }

    BOARD_DATA *board = find_board_by_filename( filename );
    if ( !board )
    {
        send_to_char( "Board not found.\n", ch );
        return;
    }

    BOARD_DATA updated = *board;
    char *BOARD_DATA::*string_field = NULL;
    sh_int *level = NULL;
    bool append_names = false;
    bool rename_board = false;
    if ( !str_cmp( field, "read_group" ) )          string_field = &BOARD_DATA::read_group;
    else if ( !str_cmp( field, "post_group" ) )     string_field = &BOARD_DATA::post_group;
    else if ( !str_cmp( field, "extra_readers" ) )
    {
        string_field = &BOARD_DATA::extra_readers;
        append_names = true;
    }
    else if ( !str_cmp( field, "extra_removers" ) )
    {
        string_field = &BOARD_DATA::extra_removers;
        append_names = true;
    }
    else if ( !str_cmp( field, "filename" ) )
    {
        if ( value_arg != board->note_file && !new_board_filename( ch, value_arg ) )
            return;
        string_field = &BOARD_DATA::note_file;
        rename_board = value_arg != board->note_file;
    }
    else if ( !str_cmp( field, "read" ) )           level = &updated.min_read_level;
    else if ( !str_cmp( field, "post" ) )           level = &updated.min_post_level;
    else if ( !str_cmp( field, "remove" ) )         level = &updated.min_remove_level;

    if ( string_field )
    {
        if ( value_arg.empty() )
        {
            send_to_char( "No value specified.\n", ch );
            return;
        }
        if ( string_field != &BOARD_DATA::note_file )
        {
            smash_tilde( value_arg );
            if ( !str_cmp( value_arg, "none" ) )
                value_arg.clear();
            else if ( append_names && *(board->*string_field) )
                value_arg = std::string( board->*string_field ) + " " + value_arg;
        }
        updated.*string_field = str_dup( value_arg );
    }
    else if ( level || !str_cmp( field, "vnum" ) || !str_cmp( field, "maxpost" )
                    || !str_cmp( field, "type" ) )
    {
        const int value = strtoi( value_arg );
        if ( level && value >= 0 && value <= MAX_LEVEL )
            *level = value;
        else if ( !str_cmp( field, "maxpost" ) && value >= 1 && value <= 1000 )
            updated.max_posts = value;
        else if ( !str_cmp( field, "type" ) && value >= BOARD_NOTE && value <= BOARD_MAIL )
            updated.type = value;
        else if ( !str_cmp( field, "vnum" ) )
        {
            if ( !get_obj_index( value ) )
            {
                send_to_char( "No such object.\n", ch );
                return;
            }
            updated.board_obj = value;
        }
        else
        {
            send_to_char( "Value out of range.\n", ch );
            return;
        }
    }
    else
    {
        do_bset( ch, NULL );
        return;
    }

    // Keep the old note file available until the board list points at the new one.
    const bool copy_notes = rename_board && updated.first_note;
    const bool copied = copy_notes && write_board( &updated );
    if ( (copy_notes && !copied) || !write_boards_txt( board, &updated ) )
    {
        if ( copied )
        {
            const std::string new_note_path = std::string( BOARD_DIR ) + updated.note_file;
            if ( unlink( new_note_path.c_str() ) != 0 )
                perror( new_note_path.c_str() );
        }
        if ( string_field )
            STR_DISPOSE( updated.*string_field );
        send_to_char( "Could not save the board change.\n", ch );
        return;
    }

    if ( string_field )
        STR_DISPOSE( board->*string_field );
    *board = updated;
    send_to_char( "Done.\n", ch );
}


void do_bstat( CHAR_DATA *ch, char *argument )
{
    BOARD_DATA *board;
    bool found;
    std::string arg;
    
    one_argument( argument ? argument : "", arg );

    if ( arg.empty() )
    {
	send_to_char( "Usage: bstat <board filename>\n", ch );
	return;
    }

    found = FALSE;
    for ( board = first_board; board; board = board->next )
	if ( !str_cmp( arg, board->note_file ) )
	{
	  found = TRUE;
	  break;
	}
    if ( !found )
    {
	send_to_char( "Board not found.\n", ch );
	return;
    }

    ch_printf( ch, "%-12s Vnum: %5d Read: %2d Post: %2d Rmv: %2d Max: %2d Posts: %d Type: %d\n",
		board->note_file,	 board->board_obj,
		board->min_read_level,	 board->min_post_level,
		board->min_remove_level, board->max_posts, 
                board->num_posts, board->type );

    ch_printf( ch, "Read_group: %-15s Post_group: %-15s \nExtra_readers: %-10s\n",
		board->read_group, board->post_group, board->extra_readers );
    return;
}


void do_boards( CHAR_DATA *ch, char *argument )
{
    BOARD_DATA *board;

    if ( !first_board )
    {
	send_to_char( "There are no boards.\n", ch );
	return;
    }

    set_char_color( AT_NOTE, ch );
    for ( board = first_board; board; board = board->next )
	ch_printf( ch, "%-16s Vnum: %5d Read: %2d Post: %2d Rmv: %2d Max: %2d Posts: %d Type: %d\n",
		board->note_file,	 board->board_obj,
		board->min_read_level,	 board->min_post_level,
		board->min_remove_level, board->max_posts, board->num_posts, 
		board->type);
}

void mail_count(CHAR_DATA *ch)
{
  BOARD_DATA *board;
  NOTE_DATA *note;
  int cnt = 0;

  if ( !ch )
    return;
  
  for ( board = first_board; board; board = board->next )
    if ( board->type == BOARD_MAIL && can_read(ch, board) )
      for ( note = board->first_note; note; note = note->next )
        if ( is_note_to(ch, note) )
          ++cnt;
  if ( cnt )
    ch_printf(ch, "You have %d mail messages waiting.\n", cnt);
  return;
}

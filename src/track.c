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
*			 Tracking/hunting module			   *
****************************************************************************/

#include <sys/types.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <queue>
#include <unordered_set>
#include "mud.h"

#define BFS_ERROR	   -1
#define BFS_ALREADY_THERE  -2
#define BFS_NO_PATH	   -3

#define TRACK_THROUGH_DOORS

extern sh_int	top_room;

bool mob_snipe( CHAR_DATA *ch , CHAR_DATA *victim);
ch_ret  one_hit             args( ( CHAR_DATA *ch, CHAR_DATA *victim, int dt ) );
ROOM_INDEX_DATA *generate_exit( ROOM_INDEX_DATA *in_room, EXIT_DATA **pexit );

/* You can define or not define TRACK_THOUGH_DOORS, above, depending on
   whether or not you want track to find paths which lead through closed
   or hidden doors.
*/

int find_first_step(ROOM_INDEX_DATA *src, ROOM_INDEX_DATA *target, int maxdist )
{
   if ( !src || !target )
   {
      bug("Illegal value passed to find_first_step (track.c)" );
      return BFS_ERROR;
   }

   if (src == target)
      return BFS_ALREADY_THERE;

   if ( maxdist < 1 || src->area != target->area )
      return BFS_NO_PATH;

   struct Step
   {
      ROOM_INDEX_DATA *room;
      int first_dir;
      int distance;
   };
   constexpr size_t max_visited_rooms = 100000;
   std::unordered_set<ROOM_INDEX_DATA*> visited;
   std::queue<Step> q;

   visited.insert(src);
   q.push({src, BFS_NO_PATH, 0});
   while (!q.empty())
   {
      const Step step = q.front();
      q.pop();
      if (step.distance >= maxdist)
         continue;

      for (sh_int direction = 0; direction < 10; ++direction)
      {
         EXIT_DATA *exit = get_exit(step.room, direction);
         if (!exit || !exit->to_room)
            continue;
#ifndef TRACK_THROUGH_DOORS
         if (IS_SET(exit->exit_info, EX_CLOSED))
            continue;
#endif
         const int first_dir = step.distance == 0 ? direction : step.first_dir;
         if (exit->to_room == target)
            return first_dir;
         if (step.distance + 1 < maxdist && visited.insert(exit->to_room).second)
         {
            if (visited.size() > max_visited_rooms)
               return BFS_NO_PATH;
            q.push({exit->to_room, first_dir, step.distance + 1});
         }
      }
   }

   return BFS_NO_PATH;
}


void do_track( CHAR_DATA *ch, char *argument )
{
   CHAR_DATA *vict;
   std::string arg;
   std::string buf;
   int dir, maxdist;

   if ( !IS_NPC(ch) && !ch->pcdata->learned[gsn_track] )
   {
	send_to_char("You do not know of this skill yet.\n", ch );
	return;
   }

   one_argument(argument, arg);
   if ( arg.empty() ) {
      send_to_char("Whom are you trying to track?\n", ch);
      return;
   }

   WAIT_STATE( ch, skill_table[gsn_track]->beats );

   if (!(vict = get_char_world(ch, arg))) {
      send_to_char("You can't sense a trail from here.\n", ch);
      return;
   }

   maxdist = 100 + ch->top_level * 30;

   if ( !IS_NPC(ch) )
     maxdist = (maxdist * ch->pcdata->learned[gsn_track]) / 100;

   dir = find_first_step(ch->in_room, vict->in_room, maxdist);
   switch(dir) {
      case BFS_ERROR:
         send_to_char("Hmm... something seems to be wrong.\n", ch);
         break;
      case BFS_ALREADY_THERE:
         send_to_char("You're already in the same room!\n", ch);
         break;
      case BFS_NO_PATH:
         buf = "You can't sense a trail from here.\n";
         send_to_char(buf, ch);
	 learn_from_failure( ch, gsn_track );
         break;
      default:
         ch_printf(ch, "You sense a trail %s from here...\n", dir_name[dir]);
	 learn_from_success( ch, gsn_track );
         break;
   }
}


void found_prey( CHAR_DATA *ch, CHAR_DATA *victim )
{
     std::string buf;
     std::string victname;

     

     if (victim == NULL)
     {
	bug("Found_prey: null victim");
	return;
     }
     
     if (ch == NULL)
     {
	bug("Found_prey: null ch");
	return;
     }

     if ( ch->in_room == NULL || victim->in_room == NULL )
     {
        bug( "Found_prey: null in_room" );
        return;
     }

     victname = IS_NPC( victim ) ? victim->short_descr : victim->name;

     if ( !can_see(ch, victim) )
     {
        if ( number_percent( ) < 90 )
	  return;
	switch( number_bits( 2 ) )
 	{
	case 0: buf = "Don't make me find you!";
		do_say( ch, (char*)buf.c_str() );
	        break;
	case 1: act( AT_ACTION, "$n sniffs around the room for someone.", ch, NULL, victim, TO_NOTVICT );
		act( AT_ACTION, "You sniff around the room for someone.", ch, NULL, victim, TO_CHAR );
		act( AT_ACTION, "$n sniffs around the room for someone.", ch, NULL, victim, TO_VICT );
		buf = "I can smell your blood!";
		do_say( ch, (char*)buf.c_str() );
		break;
	case 2: buf = "I'm going to tear you apart!";
		do_yell( ch, (char*)buf.c_str() );
		break;
	case 3: do_say( ch, "Just wait until I find you...");
		break;
        }
	return;
     }

     if ( BV_IS_SET( ch->in_room->room_flags, ROOM_SAFE ) )
     {
	if ( number_percent( ) < 90 )
	  return;
	switch( number_bits( 2 ) )
	{
	case 0:	do_say( ch, "C'mon out, you coward!" );
		buf = victname + " is a bloody coward!";
		do_yell( ch, (char*)buf.c_str() );
		break;
	case 1: 
      buf = str_printf( "Let's take this outside, %.30s", victname );
		do_say( ch, (char*)buf.c_str() );
		break;
	case 2: 
      buf = str_printf( "%.30s is a yellow-bellied wimp!", victname );
		do_yell( ch, (char*)buf.c_str() );
		break;
	case 3: act( AT_ACTION, "$n takes a few swipes at someone.", ch, NULL, victim, TO_NOTVICT );
		act( AT_ACTION, "You try to take a few swipes someone.", ch, NULL, victim, TO_CHAR );
		act( AT_ACTION, "$n takes a few swipes at you.", ch, NULL, victim, TO_VICT );
		break;
	}
	return;
     }

     switch( number_bits( 2 ) )
     {
     case 0: 
        buf = "Your blood is mine!";
	     do_yell( ch, (char*)buf.c_str() );
	     break;
     case 1: buf = "Alas, we meet again!";
     	     do_say( ch, (char*)buf.c_str() );
     	     break;
     case 2: buf = "What do you want on your tombstone?";
     	     do_say( ch, (char*)buf.c_str() );
     	     break;
     case 3: act( AT_ACTION, "$n lunges at $N from out of nowhere!", ch, NULL, victim, TO_NOTVICT );
	     act( AT_ACTION, "You lunge at $N catching $M off guard!", ch, NULL, victim, TO_CHAR );
	     act( AT_ACTION, "$n lunges at you from out of nowhere!", ch, NULL, victim, TO_VICT );
     }
     if ( char_died(ch) || char_died(victim) )
        return;
     stop_hunting( ch );
     set_fighting( ch, victim );
     multi_hit(ch, victim, TYPE_UNDEFINED);
     return;
} 

void hunt_victim( CHAR_DATA *ch )
{
   bool found;
   CHAR_DATA *tmp;
   sh_int ret;

   if (!ch || !ch->hunting || !ch->hunting->who || !ch->in_room )
      return;

   /* make sure the char still exists */
   for (found = FALSE, tmp = first_char; tmp && !found; tmp = tmp->next)
      if (ch->hunting->who == tmp)
         found = TRUE;

   if (!found || char_died(ch->hunting->who) || !ch->hunting->who->in_room)
   {
      do_say(ch, "Damn!  My prey is gone!!" );
      stop_hunting( ch );
      return;
   }

   if ( ch->in_room == ch->hunting->who->in_room )
   {
     if ( ch->fighting )
       return;
     found_prey( ch, ch->hunting->who );
     return;
   }
   
/* hunting with snipe */ 
   {
	OBJ_DATA *wield;
	  
        wield = get_eq_char( ch, WEAR_WIELD );
	if ( wield != NULL && BV_IS_SET(wield->objflags,WEAPON_BLASTER)  )
	{
	  if ( mob_snipe( ch, ch->hunting->who ) == TRUE ) 
	   return;
        }
        else if ( !BV_IS_SET( ch->act, ACT_DROID ) )
           do_hide ( ch, "" );
   }

   if ( char_died(ch) || !ch->in_room || !ch->hunting || !ch->hunting->who || !ch->hunting->who->in_room )
      return;
   
   ret = find_first_step(ch->in_room, ch->hunting->who->in_room, 5000);
   if ( ret == BFS_NO_PATH )
   {
       EXIT_DATA *pexit;
       int attempt;

       ret = BFS_NO_PATH;
       for ( attempt = 0; attempt < 25; attempt++ )
       {
	  sh_int door = number_door( );
	  if ( ( pexit = get_exit(ch->in_room, door) ) == NULL
	  ||   !pexit->to_room
	  || IS_SET(pexit->exit_info, EX_CLOSED)
	  || BV_IS_SET(pexit->to_room->room_flags, ROOM_NO_MOB) )
	     continue;
	  ret = door;
	  break;
       }
   }
   if ( ret < 0)
   {
      do_say( ch, "Damn!  Lost my prey!" );
      stop_hunting( ch );
      return;
   }
   else
   {
      EXIT_DATA *pexit = get_exit( ch->in_room, ret );
      if ( !pexit )
      {
         stop_hunting( ch );
         return;
      }
      move_char( ch, pexit, FALSE );
      if ( char_died(ch) )
          return;
      if ( !ch->hunting )
      {
        if ( !ch->in_room )
        {
          ROOM_INDEX_DATA *limbo = get_room_index( ROOM_VNUM_LIMBO );
          bug( "Hunt_victim: no ch->in_room!  Mob #%d, name: %s.  Placing mob in limbo.",
                   ch->pIndexData ? ch->pIndexData->vnum : -1,
                   ch->name ? ch->name : "(null)" );
          if ( limbo )
             char_to_room( ch, limbo );
          return;
        } 
	do_say( ch, "Damn!  Lost my prey!" );
	return;
      }
      for (found = FALSE, tmp = first_char; tmp && !found; tmp = tmp->next)
         if (ch->hunting->who == tmp)
            found = TRUE;
      if ( !found || char_died(ch->hunting->who) )
      {
         stop_hunting( ch );
         return;
      }
      if (ch->in_room && ch->in_room == ch->hunting->who->in_room)
	found_prey( ch, ch->hunting->who );
      return;
   }
}

bool mob_snipe( CHAR_DATA *ch, CHAR_DATA *victim )
{
   sh_int            dir, dist;
   sh_int            max_dist = 3;
   EXIT_DATA       * pexit;
   ROOM_INDEX_DATA * was_in_room;
   ROOM_INDEX_DATA * to_room;
   std::string buf;
   bool              pfound = FALSE;

 if ( !ch || !victim || !ch->in_room || !victim->in_room )
        return FALSE;
   
 if ( BV_IS_SET( ch->in_room->room_flags, ROOM_SAFE ) )
	return FALSE;
   
 for ( dir = 0 ; dir < 10 ; dir++ )
 {  
   if ( ( pexit = get_exit( ch->in_room, dir ) ) == NULL )
     continue;

   if ( IS_SET( pexit->exit_info, EX_CLOSED ) )
     continue;
     
   was_in_room = ch->in_room;
   
   for ( dist = 0; dist <= max_dist; dist++ )   
   {
     if ( IS_SET( pexit->exit_info, EX_CLOSED ) )
        break; 
     
     if ( !pexit->to_room )
        break;
     
    to_room = NULL;
    if ( pexit->distance > 1 )
       to_room = generate_exit( ch->in_room , &pexit );
    
    if ( to_room == NULL )
       to_room = pexit->to_room;
    
     char_from_room( ch );
     char_to_room( ch, to_room );    
     

     if ( ch->in_room == victim->in_room )
     {
        pfound = TRUE;
        break;
     }

     if ( ( pexit = get_exit( ch->in_room, dir ) ) == NULL )
        break;
            
   }
   
   if ( ch->in_room )
      char_from_room( ch );
   char_to_room( ch, was_in_room );    
       
   if ( !pfound )
       continue;
   
    if ( !victim->in_room )
	return FALSE;

    if ( BV_IS_SET( victim->in_room->room_flags, ROOM_SAFE ) )
	return FALSE;
 
    if ( is_safe( ch, victim ) )
	return FALSE;
    
    if ( IS_AFFECTED(ch, AFF_CHARM) && ch->master == victim )
	return FALSE;

    if ( ch->position == POS_FIGHTING )
	return FALSE;
	
    if ( !can_see(ch, victim))
        return FALSE;
    
    switch ( dir )
    {
        case 0:
        case 1:
           dir += 2;
           break;
        case 2:
        case 3:
           dir -= 2;
           break;
        case 4:
        case 7:
           dir += 1;
           break;
        case 5:
        case 8:
           dir -= 1;
           break;
        case 6:
           dir += 3;
           break;
        case 9:
           dir -=3;
           break;
    }
    
    char_from_room( ch );
    char_to_room( ch, victim->in_room );    
                
       buf = "A blaster shot fires at you from the " + std::string(dir_name[dir]) + ".";
       act( AT_ACTION, buf.c_str(), victim, NULL, ch, TO_CHAR );      
       act( AT_ACTION, "You fire at $N.", ch, NULL, victim, TO_CHAR );         
       buf = "A blaster shot fires at $N from the " + std::string(dir_name[dir]) + ".";
       act( AT_ACTION, buf.c_str(), ch, NULL, victim, TO_NOTVICT );  
                                                   
       one_hit( ch, victim, TYPE_UNDEFINED );  
       
       if ( char_died(ch) )
          return TRUE;
       
       stop_fighting( ch , TRUE );

       if ( !victim || char_died(victim) || victim->hit < 0 )
       {
              stop_hunting( ch );
              stop_hating( ch );
       }
       
    if ( ch->in_room )
       char_from_room( ch );
    if ( was_in_room )
       char_to_room( ch, was_in_room );    
     
    return TRUE;
 }  
 
 return FALSE;
}

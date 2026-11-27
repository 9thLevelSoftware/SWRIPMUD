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
*			Specific object creation module			   *
****************************************************************************/

#include <sys/types.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "mud.h"

void format_object_description( char *&description, char placeholder, const std::string &value )
{
    if ( !description )
        return;

    std::string result;
    bool substituted = false;
    for ( const char *p = description; *p; ++p )
    {
        if ( *p == '%' && p[1] == '%' )
        {
            result += '%';
            ++p;
        }
        else if ( !substituted && *p == '%' && p[1] == placeholder )
        {
            result += value;
            substituted = true;
            ++p;
        }
        else
            result += *p;
    }
    if ( result.size() >= MAX_STRING_LENGTH )
        result.resize(MAX_STRING_LENGTH - 1);
    STRFREE(description);
    description = STRALLOC(result);
}


/*
 * Make a fire.
 */
void make_fire(ROOM_INDEX_DATA *in_room, sh_int timer)
{
    OBJ_INDEX_DATA *pObjIndex;
    OBJ_DATA *fire;

    if ( !in_room )
    {
	bug( "make_fire: null in_room" );
	return;
    }

    pObjIndex = get_obj_index( OBJ_VNUM_FIRE );
    if ( !pObjIndex )
    {
	bug( "make_fire: missing OBJ_VNUM_FIRE (%d)", OBJ_VNUM_FIRE );
	return;
    }

    fire = create_object( pObjIndex, 0 );
    fire->timer = number_fuzzy(timer);
    obj_to_room( fire, in_room );
    return;
}

/*
 * Make a trap.
 */
OBJ_DATA *make_trap(int v0, int v1, int v2, int v3)
{
    OBJ_INDEX_DATA *pObjIndex;
    OBJ_DATA *trap;

    pObjIndex = get_obj_index( OBJ_VNUM_TRAP );
    if ( !pObjIndex )
    {
	bug( "make_trap: missing OBJ_VNUM_TRAP (%d)", OBJ_VNUM_TRAP );
	return NULL;
    }

    trap = create_object( pObjIndex, 0 );
    trap->timer = 0;
    trap->value[0] = v0;
    trap->value[1] = v1;
    trap->value[2] = v2;
    trap->value[3] = v3;
    return trap;
}


/*
 * Turn an object into scraps.		-Thoric
 */
void make_scraps( OBJ_DATA *obj )
{
  OBJ_INDEX_DATA *pObjIndex;
  OBJ_DATA  *scraps, *tmpobj;
  CHAR_DATA *ch = NULL;
  const char *short_d;

  if ( !obj )
      return;

  separate_obj( obj );
  pObjIndex = get_obj_index( OBJ_VNUM_SCRAPS );
  if ( !pObjIndex )
  {
      bug( "make_scraps: missing OBJ_VNUM_SCRAPS (%d)", OBJ_VNUM_SCRAPS );
      if ( (obj->item_type == ITEM_CONTAINER
      ||    obj->item_type == ITEM_CORPSE_PC
      ||    obj->item_type == ITEM_CORPSE_NPC
      ||    obj->item_type == ITEM_DROID_CORPSE) && obj->first_content )
      {
	  if ( obj->carried_by )
	      empty_obj( obj, NULL, obj->carried_by->in_room );
	  else if ( obj->in_room )
	      empty_obj( obj, NULL, obj->in_room );
	  else if ( obj->in_obj )
	      empty_obj( obj, obj->in_obj, NULL );
      }
      extract_obj( obj );
      return;
  }

  scraps	= create_object( pObjIndex, 0 );
  scraps->timer = number_range( 5, 15 );

  /* don't make scraps of scraps of scraps of ... */
  if ( obj->pIndexData && obj->pIndexData->vnum == OBJ_VNUM_SCRAPS )
  {
     STRFREE( scraps->short_descr );
     scraps->short_descr = STRALLOC( "some debris" );
     STRFREE( scraps->description );
     scraps->description = STRALLOC( "Bits of debris lie on the ground here." );
  }
  else
  {
     short_d = obj->short_descr ? obj->short_descr : "something";
     format_object_description(scraps->short_descr, 's', short_d);
     format_object_description(scraps->description, 's', short_d);
  }

  if ( obj->carried_by )
  {
    act( AT_OBJECT, "$p falls to the ground in scraps!",
		  obj->carried_by, obj, NULL, TO_CHAR );
    if ( obj == get_eq_char( obj->carried_by, WEAR_WIELD )
    &&  (tmpobj = get_eq_char( obj->carried_by, WEAR_DUAL_WIELD)) != NULL )
       tmpobj->wear_loc = WEAR_WIELD;

    if ( obj->carried_by->in_room )
	obj_to_room( scraps, obj->carried_by->in_room );
    else
	extract_obj( scraps );
  }
  else
  if ( obj->in_room )
  {
    if ( (ch = obj->in_room->first_person ) != NULL )
    {
      act( AT_OBJECT, "$p is reduced to little more than scraps.",
	   ch, obj, NULL, TO_ROOM );
      act( AT_OBJECT, "$p is reduced to little more than scraps.",
	   ch, obj, NULL, TO_CHAR );
    }
    obj_to_room( scraps, obj->in_room);
  }
  else
  if ( obj->in_obj )
  {
    obj_to_obj( scraps, obj->in_obj );
  }
  else
  {
    extract_obj( scraps );
  }
  if ( (obj->item_type == ITEM_CONTAINER
  ||   obj->item_type == ITEM_CORPSE_PC
  ||   obj->item_type == ITEM_CORPSE_NPC
  ||   obj->item_type == ITEM_DROID_CORPSE) && obj->first_content )
  {
    if ( ch && ch->in_room )
    {
	act( AT_OBJECT, "The contents of $p fall to the ground.",
	   ch, obj, NULL, TO_ROOM );
	act( AT_OBJECT, "The contents of $p fall to the ground.",
	   ch, obj, NULL, TO_CHAR );
    }
    if ( obj->carried_by )
	empty_obj( obj, NULL, obj->carried_by->in_room );
    else
    if ( obj->in_room )
	empty_obj( obj, NULL, obj->in_room );
    else
    if ( obj->in_obj )
	empty_obj( obj, obj->in_obj, NULL );
  }
  extract_obj( obj );
}


/*
 * Make a corpse out of a character.
 */
void make_corpse( CHAR_DATA *ch, CHAR_DATA *killer )
{
    char buf[MAX_STRING_LENGTH];
    OBJ_INDEX_DATA *pObjIndex;
    OBJ_DATA *corpse;
    OBJ_DATA *obj;
    OBJ_DATA *obj_next;
    OBJ_DATA *money;
    const char *name;

    if ( !ch )
	return;

    if ( IS_NPC(ch) )
    {
	name		= ch->short_descr ? ch->short_descr : (ch->name ? ch->name : "someone");
        if ( BV_IS_SET ( ch->act , ACT_DROID ) )
           pObjIndex	= get_obj_index(OBJ_VNUM_DROID_CORPSE);
	else   
	   pObjIndex	= get_obj_index(OBJ_VNUM_CORPSE_NPC);
	if ( !pObjIndex )
	{
	    bug( "make_corpse: missing NPC corpse object index" );
	    return;
	}
	corpse		= create_object(pObjIndex, 0);
	corpse->timer	= 6;
	if ( ch->gold > 0 )
	{
	    if ( ch->in_room && ch->in_room->area )
	      ch->in_room->area->gold_looted += ch->gold;
	    if ( (money = create_money( ch->gold )) != NULL )
	      obj_to_obj( money, corpse );
	    ch->gold = 0;
	}

/* Cannot use these!  They are used.
	corpse->value[0] = (int)ch->pIndexData->vnum;
	corpse->value[1] = (int)ch->max_hit;
*/
/*	Using corpse cost to cheat, since corpses not sellable */
	corpse->cost     = ch->pIndexData ? (-(int)ch->pIndexData->vnum) : 0;
        corpse->value[2] = corpse->timer; 
    }
    else
    {
	name		= ch->name ? ch->name : "someone";
	pObjIndex	= get_obj_index(OBJ_VNUM_CORPSE_PC);
	if ( !pObjIndex )
	{
	    bug( "make_corpse: missing OBJ_VNUM_CORPSE_PC (%d)", OBJ_VNUM_CORPSE_PC );
	    return;
	}
	corpse		= create_object(pObjIndex, 0);
	corpse->timer	= 40;
        corpse->value[2] = (int)(corpse->timer/8);
	corpse->value[3] = 0;
	if ( ch->gold > 0 )
	{
	    if ( ch->in_room && ch->in_room->area )
	      ch->in_room->area->gold_looted += ch->gold;
	    if ( (money = create_money( ch->gold )) != NULL )
	      obj_to_obj( money, corpse );
	    ch->gold = 0;
	}
    }

    /* Added corpse name - make locate easier , other skills */
    SPRINTF( buf, "corpse %s", name );
    STRFREE( corpse->name );
    corpse->name = STRALLOC( buf );

    format_object_description(corpse->short_descr, 's', name);
    format_object_description(corpse->description, 's', name);

    for ( obj = ch->first_carrying; obj; obj = obj_next )
    {
	obj_next = obj->next_content;
	obj_from_char( obj );
	if ( IS_OBJ_STAT( obj, ITEM_INVENTORY )
	  || IS_OBJ_STAT( obj, ITEM_DEATHROT ) )
	    extract_obj( obj );
	else
	    obj_to_obj( obj, corpse );
    }

    if ( ch->in_room )
	obj_to_room( corpse, ch->in_room );
    else
    {
	bug( "make_corpse: NULL ch->in_room for %s", ch->name ? ch->name : "(null)" );
	extract_obj( corpse );
    }
    return;
}



void make_blood( CHAR_DATA *ch )
{
	OBJ_INDEX_DATA *pObjIndex;
	OBJ_DATA *obj;

	if ( !ch || !ch->in_room )
	    return;

	pObjIndex = get_obj_index( OBJ_VNUM_BLOOD );
	if ( !pObjIndex )
	{
	    bug( "make_blood: missing OBJ_VNUM_BLOOD (%d)", OBJ_VNUM_BLOOD );
	    return;
	}

	obj		= create_object( pObjIndex, 0 );
	obj->timer	= number_range( 2, 4 );
	obj->value[1]   = number_range( 3, UMAX( 3, UMIN(5, ch->top_level) ) );
	obj_to_room( obj, ch->in_room );
}


void make_bloodstain( CHAR_DATA *ch )
{
	OBJ_INDEX_DATA *pObjIndex;
	OBJ_DATA *obj;

	if ( !ch || !ch->in_room )
	    return;

	pObjIndex = get_obj_index( OBJ_VNUM_BLOODSTAIN );
	if ( !pObjIndex )
	{
	    bug( "make_bloodstain: missing OBJ_VNUM_BLOODSTAIN (%d)", OBJ_VNUM_BLOODSTAIN );
	    return;
	}

	obj		= create_object( pObjIndex, 0 );
	obj->timer	= number_range( 1, 2 );
	obj_to_room( obj, ch->in_room );
}


/*
 * make some coinage
 */
OBJ_DATA *create_money( int amount )
{
    OBJ_INDEX_DATA *pObjIndex;
    OBJ_DATA *obj;

    if ( amount <= 0 )
    {
	bug( "Create_money: zero or negative money %d.", amount );
	amount = 1;
    }

    if ( amount == 1 )
    {
	pObjIndex = get_obj_index( OBJ_VNUM_MONEY_ONE );
	if ( !pObjIndex )
	{
	    bug( "Create_money: missing OBJ_VNUM_MONEY_ONE (%d)", OBJ_VNUM_MONEY_ONE );
	    return NULL;
	}
	obj = create_object( pObjIndex, 0 );
    }
    else
    {
	pObjIndex = get_obj_index( OBJ_VNUM_MONEY_SOME );
	if ( !pObjIndex )
	{
	    bug( "Create_money: missing OBJ_VNUM_MONEY_SOME (%d)", OBJ_VNUM_MONEY_SOME );
	    return NULL;
	}
	obj = create_object( pObjIndex, 0 );
	format_object_description(obj->short_descr, 'd', std::to_string(amount));
	obj->value[0]	 = amount;
    }

    return obj;
}

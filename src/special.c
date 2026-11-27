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
*			   "Special procedure" module			   *
****************************************************************************/

#include <sys/types.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "mud.h"

/* jails for wanted flags */

#define ROOM_JAIL_MON_CALAMARI_1   21148
#define ROOM_JAIL_MON_CALAMARI_2   21149
#define ROOM_JAIL_CORUSCANT        9000
#define ROOM_JAIL_ADARI        29108
#define ROOM_JAIL_RODIA            0
#define ROOM_JAIL_RYLOTH           0
#define ROOM_JAIL_GAMORR           0
#define ROOM_JAIL_TATOOINE         0
#define ROOM_JAIL_BYSS             0
#define ROOM_JAIL_NAL_HUTTA        0
#define ROOM_JAIL_KASHYYYK         0
#define ROOM_JAIL_HONOGHR          0
#define ROOM_JAIL_ENDOR		   0
#define ROOM_JAIL_ROCHE		   0
#define ROOM_JAIL_AF_EL		   0
#define ROOM_JAIL_TRANDOSH	   0
#define ROOM_JAIL_CHAD		   0
#define ROOM_JAIL_QUARREN_1     6931
#define ROOM_JAIL_QUARREN_2     6932

bool  remove_obj      args( ( CHAR_DATA *ch, int iWear, bool fReplace ) );

/*
 * The following special functions are available for mobiles.
 */
DECLARE_SPEC_FUN(	spec_jedi		);
DECLARE_SPEC_FUN( spec_jedi_healer );
DECLARE_SPEC_FUN(	spec_dark_jedi       	);
DECLARE_SPEC_FUN(	spec_fido		);
DECLARE_SPEC_FUN(	spec_guardian		);
DECLARE_SPEC_FUN(	spec_janitor		);
DECLARE_SPEC_FUN(	spec_poison		);
DECLARE_SPEC_FUN(	spec_thief		);
DECLARE_SPEC_FUN(       spec_auth               );
DECLARE_SPEC_FUN(       spec_stormtrooper       );
DECLARE_SPEC_FUN(       spec_rebel_trooper );
DECLARE_SPEC_FUN(	spec_customs_smut	);
DECLARE_SPEC_FUN(	spec_customs_alcohol	);
DECLARE_SPEC_FUN(	spec_customs_weapons	);
DECLARE_SPEC_FUN(	spec_customs_spice	);
DECLARE_SPEC_FUN(	spec_police_attack	);
DECLARE_SPEC_FUN(	spec_police_undercover	);
DECLARE_SPEC_FUN(	spec_police_jail	);
DECLARE_SPEC_FUN(	spec_police_fine	);
DECLARE_SPEC_FUN(	spec_police     	);
DECLARE_SPEC_FUN(       spec_clan_guard         );
DECLARE_SPEC_FUN(       spec_newbie_pilot         );
DECLARE_SPEC_FUN(       spec_questmaster         );

/*
 * Given a name, return the appropriate spec fun.
 */
SPEC_FUN *spec_lookup( const std::string& name )
{
    if ( !str_cmp( name, "spec_jedi"	  ) ) return spec_jedi;
    if ( !str_cmp( name, "spec_jedi_healer" ) ) return spec_jedi_healer;
    if ( !str_cmp( name, "spec_dark_jedi"	  ) ) return spec_dark_jedi;
    if ( !str_cmp( name, "spec_fido"		  ) ) return spec_fido;
    if ( !str_cmp( name, "spec_guardian"	  ) ) return spec_guardian;
    if ( !str_cmp( name, "spec_janitor"		  ) ) return spec_janitor;
    if ( !str_cmp( name, "spec_poison"		  ) ) return spec_poison;
    if ( !str_cmp( name, "spec_thief"		  ) ) return spec_thief;
    if ( !str_cmp( name, "spec_questmaster"	  ) ) return spec_questmaster;
    if ( !str_cmp( name, "spec_auth"             ) ) return spec_auth;
    if ( !str_cmp( name, "spec_stormtrooper"     ) ) return spec_stormtrooper;
    if ( !str_cmp( name, "spec_rebel_trooper"  ) ) return spec_rebel_trooper;
    if ( !str_cmp( name, "spec_customs_smut"  ) ) return spec_customs_smut;
    if ( !str_cmp( name, "spec_customs_alcohol"  ) ) return spec_customs_alcohol;
    if ( !str_cmp( name, "spec_customs_weapons"  ) ) return spec_customs_weapons;
    if ( !str_cmp( name, "spec_customs_spice"  ) ) return spec_customs_spice;
    if ( !str_cmp( name, "spec_police_attack"  ) ) return spec_police_attack;
    if ( !str_cmp( name, "spec_police_jail"  ) ) return spec_police_jail;
    if ( !str_cmp( name, "spec_police_fine"  ) ) return spec_police_fine;
    if ( !str_cmp( name, "spec_police_undercover"  ) ) return spec_police_undercover;
    if ( !str_cmp( name, "spec_police"  ) ) return spec_police;
    if ( !str_cmp( name, "spec_clan_guard" ) ) return spec_clan_guard;
    if ( !str_cmp( name, "spec_newbie_pilot" ) ) return spec_newbie_pilot;
    return 0;
}

/*
 * Given a pointer, return the appropriate spec fun text.
 */
char *lookup_spec( SPEC_FUN *special )
{
    if ( special == spec_jedi	        )	return "spec_jedi";
    if ( special == spec_jedi_healer ) return "spec_jedi_healer";
    if ( special == spec_dark_jedi	)	return "spec_dark_jedi";
    if ( special == spec_fido		)	return "spec_fido";
    if ( special == spec_guardian	)	return "spec_guardian";
    if ( special == spec_janitor	)	return "spec_janitor";
    if ( special == spec_poison		)	return "spec_poison";
    if ( special == spec_thief		)	return "spec_thief";
    if ( special == spec_questmaster	)	return "spec_questmaster";
    if ( special == spec_auth           )       return "spec_auth";
    if ( special == spec_stormtrooper   )       return "spec_stormtrooper";
    if ( special == spec_rebel_trooper ) return "spec_rebel_trooper";
    if ( special == spec_customs_smut     )	return "spec_customs_smut";
    if ( special == spec_customs_weapons     )	return "spec_customs_weapons";
    if ( special == spec_customs_alcohol     )	return "spec_customs_alcohol";
    if ( special == spec_customs_spice     )	return "spec_customs_spice";
    if ( special == spec_police_attack     )	return "spec_police_attack";
    if ( special == spec_police_jail     )	return "spec_police_jail";
    if ( special == spec_police_fine     )	return "spec_police_fine";
    if ( special == spec_police_undercover    )	return "spec_police_undercover";
    if ( special == spec_police          )	return "spec_police";
    if ( special == spec_clan_guard      )      return "spec_clan_guard";
    if ( special == spec_newbie_pilot     )      return "spec_newbie_pilot";
    return "";
}

bool spec_questmaster( CHAR_DATA *ch )
{ return FALSE;
}


bool spec_newbie_pilot( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    constexpr int pluogus = 32149;
    ROOM_INDEX_DATA *shuttle = ch->in_room;
    CHAR_DATA *v_next;
    for ( CHAR_DATA *victim = shuttle->first_person; victim; victim = v_next )
    {
        v_next = victim->next_in_room;
        if ( IS_NPC(victim) || victim->position == POS_FIGHTING )
            continue;

        bool diploma = false;
        for ( OBJ_DATA *obj = victim->last_carrying; obj; obj = obj->prev_content )
        {
            if ( obj->pIndexData && obj->pIndexData->vnum == OBJ_VNUM_SCHOOL_DIPLOMA )
            {
                diploma = true;
                break;
            }
        }
        if ( !diploma )
            continue;

        int home = pluogus;
        const char *arrival = "After a brief journey the shuttle docks with the Serin Pluogus.\n\n";
        switch ( victim->race )
        {
            case RACE_MON_CALAMARI:
                home = ROOM_START_MON_CALAMARIAN;
                arrival = "After a brief journey you arrive on Mon Calamari.\n\n";
                break;
            case RACE_QUARREN:
                home = ROOM_START_QUARREN;
                arrival = "After a brief journey you arrive on Mon Calamari.\n\n";
                break;
            case RACE_JAWA:
                home = ROOM_START_JAWA;
                arrival = "After a brief journey you arrive on Tatooine.\n\n";
                break;
            case RACE_WOOKIEE:
                home = ROOM_START_WOOKIEE;
                arrival = "After a brief journey you arrive on Kashyyyk.\n\n";
                break;
            case RACE_NOGHRI:
                home = 1001; /* Nystao Spaceport */
                arrival = "After a brief journey you arrive at Nystao Spaceport on Honoghr.\n\n";
                break;
            case RACE_HUMAN:
                home = ROOM_START_HUMAN;
                arrival = "After a brief journey you arrive at the Manarai Spaceport entrance on Coruscant.\n\n";
                break;
        }

        ROOM_INDEX_DATA *destination = get_room_index( home );
        if ( !destination && home != pluogus )
        {
            destination = get_room_index( pluogus );
            arrival = "After a brief journey the shuttle docks with the Serin Pluogus.\n\n";
        }
        if ( !destination )
        {
            destination = get_room_index( ROOM_VNUM_ALTAR );
            arrival = "After a brief journey the shuttle reaches a safe destination.\n\n";
        }
        if ( !destination )
            continue;

        if ( home == pluogus && destination->vnum == pluogus )
        {
            char buf[MAX_STRING_LENGTH];
            if ( victim->race >= 0 && victim->race < MAX_RACE )
            {
                SPRINTF( buf, "Hmm, a %s.", race_table[victim->race].race_name );
                do_look( ch, victim->name );
                if ( char_died(ch) || ch->in_room != shuttle )
                    return FALSE;
                do_say( ch, buf );
                if ( char_died(ch) || ch->in_room != shuttle )
                    return FALSE;
            }
            do_say( ch, "You're home planet is a little hard to get to right now." );
            if ( char_died(ch) || ch->in_room != shuttle )
                return FALSE;
            do_say( ch, "I'll take you to the Pluogus instead." );
            if ( char_died(ch) || ch->in_room != shuttle )
                return FALSE;
            if ( char_died(victim) || victim->in_room != shuttle )
                continue;
        }

        echo_to_room( AT_ACTION, shuttle, arrival );
        const std::string departure = str_printf(
            "%s steps out and the shuttle quickly returns to the academy.\n", victim->name );
        char_from_room( victim );
        char_to_room( victim, destination );
        do_look( victim, "" );

        if ( char_died(ch) || ch->in_room != shuttle )
            return FALSE;
        echo_to_room( AT_ACTION, shuttle, departure );
    }
    return FALSE;
}

static SPELL_FUN *find_special_spell(const char *name, int &sn)
{
    sn = skill_lookup(name);
    return IS_VALID_SN(sn) ? skill_table[sn]->spell_fun : nullptr;
}

bool spec_jedi( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room || !IS_AWAKE(ch) || ch->fighting )
        return FALSE;

    CHAR_DATA *victim;
    for ( victim = ch->in_room->first_person; victim; victim = victim->next_in_room )
        if ( victim != ch && can_see(ch, victim) && number_bits(1) == 0 )
            break;

    if ( !victim )
        return FALSE;

    static const char *const spells[] = {
        "armor", "alertness", "cure blindness", "cure light",
        "cure poison", "refresh", "cure serious", "beauty"
    };
    int sn;
    SPELL_FUN *cast = find_special_spell(spells[number_bits(3)], sn);
    if ( !cast )
        return FALSE;

    cast(sn, ch->top_level, ch, victim);
    return TRUE;
}

bool spec_clan_guard( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;
    CLAN_DATA *clan;
    
    if ( !IS_AWAKE(ch) || ch->fighting )
	return FALSE;

    clan = get_clan(ch->name);

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
	if ( !can_see( ch, victim ) )
	   continue;
        if ( get_timer(victim, TIMER_RECENTFIGHT) > 0 )
	   continue;
        if ( !IS_NPC( victim ) && victim->pcdata && victim->pcdata->clan && clan && IS_AWAKE(victim)
               && (clan != victim->pcdata->clan ) 
               && ( !victim->pcdata->clan->mainclan || clan != victim->pcdata->clan->mainclan )
               && ( !clan->mainclan || clan->mainclan != victim->pcdata->clan ) )
          { 
	    do_yell( ch, "Hey your not allowed in here!" );
            multi_hit( ch, victim, TYPE_UNDEFINED );
            return TRUE;         
          }
    }

    return FALSE;
}

bool spec_customs_smut( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;
    OBJ_DATA  *obj;
    char       buf[MAX_STRING_LENGTH]; // needed for do_fun command
    long       ch_exp;
    
    if ( !IS_AWAKE(ch) || ch->position == POS_FIGHTING )
	return FALSE;

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
	
	if ( IS_NPC(victim) || victim->position == POS_FIGHTING )
	   continue;
	
	for ( obj = victim->last_carrying; obj; obj = obj->prev_content )
	{
	    if (obj->pIndexData->item_type == ITEM_SMUT)
	    {
	       if ( victim != ch && can_see( ch, victim ) && can_see_obj( ch,obj ) )
	       {
	          SPRINTF( buf , "%s is illegal contraband. I'm going to have to confiscate that.", obj->short_descr );
                  do_say( ch , buf );
                  if ( obj->wear_loc != WEAR_NONE )
                     remove_obj( victim, obj->wear_loc, TRUE );
                  separate_obj( obj );
    		  obj_from_char( obj );
    		  act( AT_ACTION, "$n confiscates $p from $N.", ch, obj, victim, TO_NOTVICT );
    		  act( AT_ACTION, "$n takes $p from you.",   ch, obj, victim, TO_VICT    );
    		  obj = obj_to_char( obj, ch );
                  BV_SET_BIT( obj->objflags , ITEM_CONTRABAND);
                  ch_exp = UMIN( obj->cost*10 , ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1) - exp_level( victim->skill_level[SMUGGLING_ABILITY])  )   );
                  ch_printf( victim, "You lose %ld experience.\n " , ch_exp );
                  gain_exp( victim, 0-ch_exp , SMUGGLING_ABILITY);
                  return TRUE;
	       }
	       else if ( can_see( ch, victim ) && !BV_IS_SET( obj->objflags , ITEM_CONTRABAND)  ) 
	       { 
                  ch_exp = UMIN( obj->cost*10 , ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1) - exp_level( victim->skill_level[SMUGGLING_ABILITY])  )   );
                  ch_printf( victim, "You receive %ld experience for smuggling %s.\n " , ch_exp , obj->short_descr );
                  gain_exp( victim, ch_exp , SMUGGLING_ABILITY );
       
	          act( AT_ACTION, "$n looks at $N suspiciously.", ch, NULL, victim, TO_NOTVICT );
    		  act( AT_ACTION, "$n look at you suspiciously.",   ch, NULL, victim, TO_VICT  );
		  separate_obj( obj );
    		  BV_SET_BIT( obj->objflags , ITEM_CONTRABAND);
	          
	          return TRUE;
	       }
	       else if ( !BV_IS_SET( obj->objflags , ITEM_CONTRABAND)  )
	       {
                  ch_exp = UMIN( obj->cost*10 , ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1) - exp_level( victim->skill_level[SMUGGLING_ABILITY])  )   );
                  ch_printf( victim, "You receive %ld experience for smuggling %s.\n " , ch_exp, obj->short_descr );
                  gain_exp( victim, ch_exp , SMUGGLING_ABILITY );
       
	          separate_obj( obj );
	          BV_SET_BIT( obj->objflags , ITEM_CONTRABAND);
	          return TRUE;
	       }
	    }
	    else if ( obj->item_type == ITEM_CONTAINER )
	    {
	        OBJ_DATA *content;
	        for ( content = obj->first_content; content; content = content->next_content )
	        {
	            if (content->pIndexData->item_type == ITEM_SMUT
	            && !BV_IS_SET( content->objflags , ITEM_CONTRABAND ) )
	            {
	               ch_exp = UMIN( content->cost*10 , ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1) - exp_level( victim->skill_level[SMUGGLING_ABILITY])  )   );
                     ch_printf( victim, "You receive %ld experience for smuggling %s.\n " , ch_exp , content->short_descr );
                       gain_exp( victim, ch_exp, SMUGGLING_ABILITY );
                   separate_obj( content );                                            
	               BV_SET_BIT( content->objflags , ITEM_CONTRABAND);
	               return TRUE;
	            }
	        }
	    }
	}
	
    }

    return FALSE;
}

bool spec_customs_weapons( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;
    OBJ_DATA  *obj;
    char       buf[MAX_STRING_LENGTH]; // needed for do_fun command
    long       ch_exp;
    
    if ( !IS_AWAKE(ch) || ch->position == POS_FIGHTING )
	return FALSE;

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
	
	if ( IS_NPC(victim) || victim->position == POS_FIGHTING )
    	   continue;
        
      if (  victim->pcdata && victim->pcdata->clan && !str_cmp(victim->pcdata->clan->name , ch->mob_clan) )  
           continue;
        	
	for ( obj = victim->last_carrying; obj; obj = obj->prev_content )
	{
	    if (obj->pIndexData->item_type == ITEM_WEAPON)
	    {
	       if ( victim != ch && can_see( ch, victim ) && can_see_obj( ch,obj ) )
	       {
	          SPRINTF( buf , "Weapons are banned from non-military usage. I'm going to have to confiscate %s.", obj->short_descr );
                  do_say( ch , buf );
                  if ( obj->wear_loc != WEAR_NONE )
                    remove_obj( victim, obj->wear_loc, TRUE );
                  separate_obj( obj );
    		  obj_from_char( obj );
    		  act( AT_ACTION, "$n confiscates $p from $N.", ch, obj, victim, TO_NOTVICT );
    		  act( AT_ACTION, "$n takes $p from you.",   ch, obj, victim, TO_VICT    );
    		  obj = obj_to_char( obj, ch );
                  BV_SET_BIT( obj->objflags , ITEM_CONTRABAND);
                  ch_exp = UMIN( obj->cost*10 , ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1) - exp_level( victim->skill_level[SMUGGLING_ABILITY])  )  );
                  ch_printf( victim, "You lose %ld experience.\n " , ch_exp );
                  gain_exp( victim, 0-ch_exp , SMUGGLING_ABILITY);
                  return TRUE;
	       }
	       else if ( can_see( ch, victim ) && !BV_IS_SET( obj->objflags , ITEM_CONTRABAND)  ) 
	       { 
                  ch_exp = UMIN( obj->cost*10 , ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1) - exp_level( victim->skill_level[SMUGGLING_ABILITY])  )   );
                  ch_printf( victim, "You receive %ld experience for smuggling %s.\n " , ch_exp, obj->short_descr );
                  gain_exp( victim, ch_exp, SMUGGLING_ABILITY );
       
	         act( AT_ACTION, "$n looks at $N suspiciously.", ch, NULL, victim, TO_NOTVICT );
    		  act( AT_ACTION, "$n look at you suspiciously.",   ch, NULL, victim, TO_VICT  );
		  separate_obj( obj );
    		  BV_SET_BIT( obj->objflags , ITEM_CONTRABAND);
	          return TRUE;
	       }
	       else if ( !BV_IS_SET( obj->objflags , ITEM_CONTRABAND)  )
	       {
                  ch_exp = UMIN( obj->cost*10 , ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1) - exp_level( victim->skill_level[SMUGGLING_ABILITY])  )   );
                  ch_printf( victim, "You receive %ld experience for smuggling %s.\n " , ch_exp , obj->short_descr);
                  gain_exp( victim, ch_exp , SMUGGLING_ABILITY);
       
	          separate_obj( obj );
	          BV_SET_BIT( obj->objflags , ITEM_CONTRABAND);
	          return TRUE;
	       }
	    }
	    else if ( obj->item_type == ITEM_CONTAINER )
	    {
	        OBJ_DATA *content;
	        for ( content = obj->first_content; content; content = content->next_content )
	        {
	            if (content->pIndexData->item_type == ITEM_WEAPON
	            && !BV_IS_SET( content->objflags , ITEM_CONTRABAND ) )
	            {
	               ch_exp = UMIN( content->cost*10 , ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1) - exp_level( victim->skill_level[SMUGGLING_ABILITY])  )   );
                     ch_printf( victim, "You receive %ld experience for smuggling %s.\n " , ch_exp , content->short_descr);
                       gain_exp( victim, ch_exp, SMUGGLING_ABILITY );
                   separate_obj( content );                       
	               BV_SET_BIT( content->objflags , ITEM_CONTRABAND);
	               return TRUE;
	            }
	        }
	    }
	}
	
    }

    return FALSE;
}

bool spec_customs_alcohol( CHAR_DATA *ch )
{   
    CHAR_DATA *victim;
    CHAR_DATA *v_next;
    OBJ_DATA  *obj;
    char       buf[MAX_STRING_LENGTH]; // needed for do_fun command
    int        liquid;
    long       ch_exp;
    
    if ( !IS_AWAKE(ch) || ch->position == POS_FIGHTING )
	return FALSE;

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
	
	if ( IS_NPC(victim) || victim->position == POS_FIGHTING )
	   continue;
	
	for ( obj = victim->last_carrying; obj; obj = obj->prev_content )
	{
	    if (obj->pIndexData->item_type == ITEM_DRINK_CON)
	    { 
	     liquid = obj->value[2];
	     if ( liquid < 0 || liquid >= LIQ_MAX )
	        liquid = obj->value[2] = 0;
	     
	     if ( liq_table[ liquid ].liq_affect[COND_DRUNK] > 0 )
	     {
	       if ( victim != ch && can_see( ch, victim ) && can_see_obj( ch,obj ) )
	       {
	          SPRINTF( buf , "%s is illegal contraband. I'm going to have to confiscate that.", obj->short_descr );
                  do_say( ch , buf );
                  if ( obj->wear_loc != WEAR_NONE )
                    remove_obj( victim, obj->wear_loc, TRUE );
                  separate_obj( obj );
    		  obj_from_char( obj );
    		  act( AT_ACTION, "$n confiscates $p from $N.", ch, obj, victim, TO_NOTVICT );
    		  act( AT_ACTION, "$n takes $p from you.",   ch, obj, victim, TO_VICT    );
    		  obj = obj_to_char( obj, ch );
                  BV_SET_BIT( obj->objflags , ITEM_CONTRABAND);
                  ch_exp = UMIN( obj->cost*10 , ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1) - exp_level( victim->skill_level[SMUGGLING_ABILITY])  )   );
                  ch_printf( victim, "You lose %ld experience. \n" , ch_exp );
                  gain_exp( victim, 0-ch_exp , SMUGGLING_ABILITY);
                  return TRUE;
	       }
            else if( can_see( ch, victim ) && !BV_IS_SET( obj->objflags, ITEM_CONTRABAND ) )
            {
               ch_exp =
                  UMIN( obj->cost * 10,
                        ( exp_level( victim->skill_level[SMUGGLING_ABILITY] + 1 ) -
                          exp_level( victim->skill_level[SMUGGLING_ABILITY] ) ) );
               ch_printf( victim, "You receive %ld experience for smuggling %s.\n ", ch_exp, obj->short_descr );
               gain_exp( victim, ch_exp, SMUGGLING_ABILITY );

               act( AT_ACTION, "$n looks at $N suspiciously.", ch, NULL, victim, TO_NOTVICT );
               act( AT_ACTION, "$n look at you suspiciously.", ch, NULL, victim, TO_VICT );
               separate_obj( obj );
               BV_SET_BIT( obj->objflags, ITEM_CONTRABAND );

               return TRUE;
            }
            else if( !BV_IS_SET( obj->objflags, ITEM_CONTRABAND ) )
            {
               ch_exp =
                  UMIN( obj->cost * 10,
                        ( exp_level( victim->skill_level[SMUGGLING_ABILITY] + 1 ) -
                          exp_level( victim->skill_level[SMUGGLING_ABILITY] ) ) );
               ch_printf( victim, "You receive %ld experience for smuggling %s.\n ", ch_exp, obj->short_descr );
               gain_exp( victim, ch_exp, SMUGGLING_ABILITY );
               separate_obj( obj );
               BV_SET_BIT( obj->objflags, ITEM_CONTRABAND );
               return TRUE;
            }
	     }
	    }
	    else if ( obj->item_type == ITEM_CONTAINER )
	    {
	        OBJ_DATA *content;
	        for ( content = obj->first_content; content; content = content->next_content )
	        {
	            if (content->pIndexData->item_type == ITEM_DRINK_CON
	            && !BV_IS_SET( content->objflags , ITEM_CONTRABAND ) )
	            {
	               liquid = content->value[2];
	               if ( liquid < 0 || liquid >= LIQ_MAX )
	                    liquid = obj->value[2] = 0;
	               if ( liq_table[ liquid ].liq_affect[COND_DRUNK] <= 0 )
	                    continue;
	                ch_exp = UMIN( content->cost*10 , 
                        ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1 ) - 
                          exp_level( victim->skill_level[SMUGGLING_ABILITY] ) ) );
                    ch_printf( victim, "You receive %ld experience for smuggling %s.\n " , ch_exp , content->short_descr);
                    gain_exp( victim, ch_exp , SMUGGLING_ABILITY);
                    separate_obj( content );                    
	                BV_SET_BIT( content->objflags , ITEM_CONTRABAND);
	                return TRUE;
	            }
	        }
	    }
	}
	
    }

    return FALSE;
}

bool spec_customs_spice( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;
    OBJ_DATA  *obj;
    char       buf[MAX_STRING_LENGTH]; // needed for do_fun command
    long       ch_exp;
    
    if ( !IS_AWAKE(ch) || ch->position == POS_FIGHTING )
	return FALSE;

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
	
	if ( IS_NPC(victim) || victim->position == POS_FIGHTING )
	   continue;
	
	for ( obj = victim->last_carrying; obj; obj = obj->prev_content )
	{
	    if (obj->pIndexData->item_type == ITEM_SPICE || obj->pIndexData->item_type  == ITEM_RAWSPICE)
	    {
	       if ( victim != ch && can_see( ch, victim ) && can_see_obj( ch,obj ) )
	       {
	          SPRINTF( buf , "%s is illegal contraband. I'm going to have to confiscate that.", obj->short_descr );
                  do_say( ch , buf );
                  if ( obj->wear_loc != WEAR_NONE )
                    remove_obj( victim, obj->wear_loc, TRUE );
                  separate_obj( obj );
    		  obj_from_char( obj );
    		  act( AT_ACTION, "$n confiscates $p from $N.", ch, obj, victim, TO_NOTVICT );
    		  act( AT_ACTION, "$n takes $p from you.",   ch, obj, victim, TO_VICT    );
    		  obj = obj_to_char( obj, ch );
                  BV_SET_BIT( obj->objflags , ITEM_CONTRABAND);
                  ch_exp = UMIN( obj->cost*10 , ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1) - exp_level( victim->skill_level[SMUGGLING_ABILITY])  )   );
                  ch_printf( victim, "You lose %ld experience. \n" , ch_exp );
                  gain_exp( victim, 0-ch_exp , SMUGGLING_ABILITY);
	          return TRUE;
	       }
	       else if ( can_see( ch, victim ) && !BV_IS_SET( obj->objflags , ITEM_CONTRABAND)  ) 
	       { 
                  ch_exp = UMIN( obj->cost*10 , ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1) - exp_level( victim->skill_level[SMUGGLING_ABILITY])  )   );
                  ch_printf( victim, "You receive %ld experience for smuggling %s. \n" , ch_exp , obj->short_descr);
                  gain_exp( victim, ch_exp , SMUGGLING_ABILITY );
       
	          act( AT_ACTION, "$n looks at $N suspiciously.", ch, NULL, victim, TO_NOTVICT );
    		  act( AT_ACTION, "$n look at you suspiciously.",   ch, NULL, victim, TO_VICT  );
		  separate_obj( obj );
    		  BV_SET_BIT( obj->objflags , ITEM_CONTRABAND);
	          return TRUE;
	       }
	       else if ( !BV_IS_SET( obj->objflags , ITEM_CONTRABAND)  )
	       {
                  ch_exp = UMIN( obj->cost*10 , ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1) - exp_level( victim->skill_level[SMUGGLING_ABILITY])  )   );
                  ch_printf( victim, "You receive %ld experience for smuggling %s. \n" , ch_exp , obj->short_descr);
                  gain_exp( victim, ch_exp , SMUGGLING_ABILITY);
       
	          separate_obj( obj );
	          BV_SET_BIT( obj->objflags , ITEM_CONTRABAND);
	          return TRUE;
	       }
	    }
	    else if ( obj->item_type == ITEM_CONTAINER )
	    {
	        OBJ_DATA *content;
	        for ( content = obj->first_content; content; content = content->next_content )
	        {
	            if ((content->item_type == ITEM_SPICE || content->item_type == ITEM_RAWSPICE)
	            && !BV_IS_SET( content->objflags , ITEM_CONTRABAND ) )
	            {
	               ch_exp = UMIN( content->cost*10 , ( exp_level( victim->skill_level[SMUGGLING_ABILITY]+1) - exp_level( victim->skill_level[SMUGGLING_ABILITY])  )   );
                     ch_printf( victim, "You receive %ld experience for smuggling %s.\n " , ch_exp , content->short_descr);
                       gain_exp( victim, ch_exp, SMUGGLING_ABILITY );
                   separate_obj( content );                       
	               BV_SET_BIT( content->objflags , ITEM_CONTRABAND);
	               return TRUE;
	            }
	        }
	    }
	}
	
    }

    return FALSE;
}

bool spec_police( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;
    char buf[MAX_STRING_LENGTH]; // needed for do_fun command
    
    if ( !IS_AWAKE(ch) || ch->fighting )
	return FALSE;

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
	if ( IS_NPC(victim) )
	   continue;
        if ( !can_see( ch, victim ) )
	   continue;
        if ( number_bits ( 1 ) == 0 ) 
	   continue;
	for ( size_t vip = 0 ; vip < PLANET_MAX ; vip++ )
          if ( BV_IS_SET ( ch->vip_flags , vip ) &&  BV_IS_SET( victim->pcdata->wanted_flags , vip) ) 
          {
              ROOM_INDEX_DATA *jail = NULL;
              
              SPRINTF( buf , "Hey you're wanted on %s!", get_flag_name(planet_flags, vip, PLANET_MAX) );
                  do_say( ch , buf );                 
              BV_REMOVE_BIT( victim->pcdata->wanted_flags , vip ); 	                   
              if ( ch->top_level >= victim->top_level ) 
                 multi_hit( ch, victim, TYPE_UNDEFINED );
              else if( vip == PLANET_ADARI )
                 jail = get_room_index( ROOM_JAIL_ADARI );
              else if( vip == PLANET_MON_CALAMARI )
                switch ( number_range(1,4) )
                {
                  case 1:
                   jail = get_room_index( ROOM_JAIL_MON_CALAMARI_1 );
                   break;
                  case 2:
                   jail = get_room_index( ROOM_JAIL_MON_CALAMARI_2 );
                   break;
                  case 3:
                   jail = get_room_index( ROOM_JAIL_QUARREN_2 );
                   break;
                  case 4:
                   jail = get_room_index( ROOM_JAIL_QUARREN_1 );
                   break;
                }
              else
              {    
                 act( AT_ACTION, "$n fines $N an enormous amount of money.", ch, NULL, victim, TO_NOTVICT );
    	         act( AT_ACTION, "$n fines you an enourmous amount of money.",   ch, NULL, victim, TO_VICT    );
    	         if( victim->in_room && victim->in_room->area )
    	           boost_economy( victim->in_room->area, (victim->gold)/2 );
    	         victim->gold /= 2;
              }
                 
              if ( jail )
              {
                BV_REMOVE_BIT( victim->pcdata->wanted_flags , vip ); 	                     
                act( AT_ACTION, "$n ushers $N off to jail.", ch, NULL, victim, TO_NOTVICT );
    	        act( AT_ACTION, "$n escorts you to jail.",   ch, NULL, victim, TO_VICT    );
    	        char_from_room( victim );
    	        char_to_room( victim , jail );
    	      } 
              return TRUE;         
          }
    
    }

    return FALSE;

}

bool spec_police_attack( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;
    char buf[MAX_STRING_LENGTH]; // needed for do_fun command
    
    if ( !IS_AWAKE(ch) || ch->fighting )
	return FALSE;

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
	if ( IS_NPC(victim) )
	   continue;
        if ( !can_see( ch, victim ) )
	   continue;
        if ( number_bits ( 1 ) == 0 ) 
	   continue;
	for ( size_t vip = 0 ; vip < PLANET_MAX ; vip++ )
          if ( BV_IS_SET ( ch->vip_flags , vip ) &&  BV_IS_SET( victim->pcdata->wanted_flags , vip) ) 
          {
              SPRINTF( buf , "Hey you're wanted on %s!", get_flag_name(planet_flags, vip, PLANET_MAX) );
                  do_say( ch , buf );                 
              BV_REMOVE_BIT( victim->pcdata->wanted_flags , vip ); 	                   
              multi_hit( ch, victim, TYPE_UNDEFINED );
              return TRUE;         
          }
    
    }

    return FALSE;

}

bool spec_police_undercover( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;
    char buf[MAX_STRING_LENGTH]; // needed for do_fun command
    
    if ( !IS_AWAKE(ch) || ch->fighting )
	return FALSE;

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
	if ( IS_NPC(victim) )
	   continue;
        if ( !can_see( ch, victim ) )
	   continue;
        if ( number_bits ( 1 ) == 0 ) 
	   continue;
	for ( size_t vip = 0 ; vip < PLANET_MAX ; vip++ )
          if ( BV_IS_SET ( ch->vip_flags , vip ) &&  BV_IS_SET( victim->pcdata->wanted_flags , vip) ) 
          {
              SPRINTF( buf , "Got you!" );
                  do_say( ch , buf );                 
              BV_REMOVE_BIT( victim->pcdata->wanted_flags , vip ); 	                   
              multi_hit( ch, victim, TYPE_UNDEFINED );
              return TRUE;         
          }
    
    }

    return FALSE;

}

bool spec_police_fine( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;
    char buf[MAX_STRING_LENGTH]; // needed for do_fun command

    if ( !IS_AWAKE(ch) || ch->fighting )
	return FALSE;

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
	if ( IS_NPC(victim) )
	   continue;
        if ( !can_see( ch, victim ) )
	   continue;
        if ( number_bits ( 1 ) == 0 ) 
	   continue;
	for ( size_t vip = 0 ; vip < PLANET_MAX ; vip++ )
          if ( BV_IS_SET ( ch->vip_flags , vip ) &&  BV_IS_SET( victim->pcdata->wanted_flags , vip) ) 
          {
              SPRINTF( buf , "Hey you're wanted on %s!", get_flag_name(planet_flags, vip, PLANET_MAX) );
                  do_say( ch , buf );
              act( AT_ACTION, "$n fines $N an enormous amount of money.", ch, NULL, victim, TO_NOTVICT );
    	      act( AT_ACTION, "$n fines you an enourmous amount of money.",   ch, NULL, victim, TO_VICT    );
    	         if( victim->in_room && victim->in_room->area )
    	           boost_economy( victim->in_room->area, (victim->gold)/2 );
    	      victim->gold /= 2;
    	      BV_REMOVE_BIT( victim->pcdata->wanted_flags , vip ); 	                   
              return TRUE;         
          }
    
    }

    return FALSE;

}

bool spec_police_jail( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room || !IS_AWAKE(ch) || ch->fighting )
        return FALSE;

    ROOM_INDEX_DATA *patrol_room = ch->in_room;
    CHAR_DATA *v_next;
    for ( CHAR_DATA *victim = patrol_room->first_person; victim; victim = v_next )
    {
        v_next = victim->next_in_room;
        if ( IS_NPC(victim) || !can_see( ch, victim ) || number_bits(1) == 0 )
            continue;

        for ( size_t vip = 0; vip < PLANET_MAX; ++vip )
        {
            if ( !BV_IS_SET(ch->vip_flags, vip) || !BV_IS_SET(victim->pcdata->wanted_flags, vip) )
                continue;

            ROOM_INDEX_DATA *jail = nullptr;
            switch ( vip )
            {
                case PLANET_ADARI:
                    jail = get_room_index( ROOM_JAIL_ADARI );
                    break;
                case PLANET_CORUSCANT:
                    jail = get_room_index( ROOM_JAIL_CORUSCANT );
                    break;
                case PLANET_MON_CALAMARI:
                {
                    constexpr int jail_vnums[] = {
                        ROOM_JAIL_MON_CALAMARI_1, ROOM_JAIL_MON_CALAMARI_2,
                        ROOM_JAIL_QUARREN_1, ROOM_JAIL_QUARREN_2
                    };
                    ROOM_INDEX_DATA *loaded_jails[sizeof(jail_vnums) / sizeof(jail_vnums[0])];
                    int count = 0;
                    for ( int vnum : jail_vnums )
                    {
                        if ( ROOM_INDEX_DATA *room = get_room_index( vnum ) )
                            loaded_jails[count++] = room;
                    }
                    if ( count > 0 )
                        jail = loaded_jails[number_range(0, count - 1)];
                    break;
                }
            }
            if ( !jail )
                continue;

            char buf[MAX_STRING_LENGTH];
            SPRINTF( buf, "Hey you're wanted on %s!", get_flag_name(planet_flags, vip, PLANET_MAX) );
            do_say( ch, buf );
            if ( char_died(ch) || ch->in_room != patrol_room
                 || char_died(victim) || victim->in_room != patrol_room )
                return TRUE;

            BV_REMOVE_BIT( victim->pcdata->wanted_flags, vip );
            act( AT_ACTION, "$n ushers $N off to jail.", ch, NULL, victim, TO_NOTVICT );
            act( AT_ACTION, "$n escorts you to jail.", ch, NULL, victim, TO_VICT );
            char_from_room( victim );
            char_to_room( victim, jail );
            return TRUE;
        }
    }
    return FALSE;
}

bool spec_jedi_healer( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room || !IS_AWAKE(ch) )
        return FALSE;

    CHAR_DATA *victim;
    for ( victim = ch->in_room->first_person; victim; victim = victim->next_in_room )
        if ( victim != ch && can_see( ch, victim ) && number_bits( 1 ) == 0 )
            break;

    if ( !victim )
        return FALSE;

    static const struct
    {
        const char *name;
        SPELL_FUN *cast;
    } spells[] = {
        { "armor", spell_smaug },
        { "good fortune", spell_smaug },
        { "cure blindness", spell_cure_blindness },
        { "cure light", spell_smaug },
        { "cure poison", spell_cure_poison },
        { "refresh", spell_smaug }
    };

    const unsigned choice = number_bits( 4 );
    if ( choice >= sizeof(spells) / sizeof(spells[0]) )
        return FALSE;

    const int sn = skill_lookup( spells[choice].name );
    if ( !IS_VALID_SN( sn ) )
        return FALSE;

    act( AT_MAGIC, "$n pauses and concentrates for a moment.", ch, NULL, NULL, TO_ROOM );
    spells[choice].cast( sn, ch->top_level, ch, victim );
    return TRUE;
}


bool spec_dark_jedi( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;
    char *spell;
    int sn;


    if ( ch->position != POS_FIGHTING )
	return FALSE;

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
	if ( who_fighting( victim ) && number_bits( 2 ) == 0 )
	    break;
    }

    if ( !victim || victim == ch )
	return FALSE;

    for ( ;; )
    {
	int min_level;

	switch ( number_bits( 4 ) )
	{
	case  0: min_level =  5; spell = "blindness";      break;
	case  1: min_level =  5; spell = "fingers of the force";    break;
	case  2: min_level =  9; spell = "choke";         break;
	case  3: min_level =  8; spell = "invade essence";       break;
	case  4: min_level = 11; spell = "force projectile";   break;
	case  6: min_level = 13; spell = "drain essence";   break;
	case  7: min_level =  4; spell = "force whip";   break; 
	case  8: min_level = 13; spell = "harm";          break;
	case  9: min_level = 9; spell = "force bolt";       break;
	case 10: min_level = 1; spell = "force spray";     break;
	default: return FALSE;
	}

	if ( ch->top_level >= min_level )
	    break;
    }

    SPELL_FUN *cast = find_special_spell(spell, sn);
    if ( !cast )
        return FALSE;
    cast(sn, ch->top_level, ch, victim);
    return TRUE;
}



bool spec_fido( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    OBJ_DATA *corpse;
    OBJ_DATA *c_next;
    OBJ_DATA *obj;
    OBJ_DATA *obj_next;

    if ( !IS_AWAKE(ch) )
	return FALSE;

    for ( corpse = ch->in_room->first_content; corpse; corpse = c_next )
    {
	c_next = corpse->next_content;
	if ( corpse->item_type != ITEM_CORPSE_NPC )
	    continue;

    act( AT_ACTION, "$n savagely devours a corpse.", ch, NULL, NULL, TO_ROOM );
	for ( obj = corpse->first_content; obj; obj = obj_next )
	{
	    obj_next = obj->next_content;
	    obj_from_obj( obj );
	    obj_to_room( obj, ch->in_room );
	}
	extract_obj( corpse );
	return TRUE;
    }

    return FALSE;
}

bool spec_stormtrooper( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;

    if ( !IS_AWAKE(ch) || ch->fighting )
	return FALSE;

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
	if ( !can_see( ch, victim ) )
	   continue;
        if ( get_timer(victim, TIMER_RECENTFIGHT) > 0 )
	   continue;
        if ( ( IS_NPC( victim ) && nifty_is_name( "rebel" , victim->name )
               && victim->fighting && who_fighting( victim ) != ch ) ||
             ( !IS_NPC( victim ) && victim->pcdata && victim->pcdata->clan && IS_AWAKE(victim)
               && nifty_is_name( "rebel" , victim->pcdata->clan->name ) ) )
          {
	      do_yell( ch, "Die Rebel Scum!" );
              multi_hit( ch, victim, TYPE_UNDEFINED );
              return TRUE;         
          }
    
    }

    return FALSE;

}  

bool spec_rebel_trooper( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;

    if ( !IS_AWAKE(ch) || ch->fighting )
	return FALSE;

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
        if ( !can_see( ch, victim ) )
	   continue;
        if ( get_timer(victim, TIMER_RECENTFIGHT) > 0 )
	   continue;
        if ( ( IS_NPC( victim ) && nifty_is_name( "imperial" , victim->name )
               && victim->fighting && who_fighting( victim ) != ch ) ||
             ( !IS_NPC( victim ) && victim->pcdata && victim->pcdata->clan && IS_AWAKE(victim) 
             && nifty_is_name( "empire" , victim->pcdata->clan->name ) ) )
          {
	      do_yell( ch, "Long live the Rebel Alliance!" );
              multi_hit( ch, victim, TYPE_UNDEFINED );
              return TRUE;         
          }
    
    }

    return FALSE;

}  


bool spec_guardian( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;
    CHAR_DATA *ech;
    int max_evil;

    if ( !IS_AWAKE(ch) || ch->fighting )
	return FALSE;

    max_evil = 300;
    ech      = NULL;

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
	if ( victim->fighting
	&&   who_fighting( victim ) != ch
	&&   victim->alignment < max_evil )
	{
	    max_evil = victim->alignment;
	    ech      = victim;
	}
    }

    if ( ech && !BV_IS_SET(ch->in_room->room_flags, ROOM_SAFE) )
    {
    act( AT_YELL, "$n screams 'PROTECT THE INNOCENT!!",
	    ch, NULL, NULL, TO_ROOM );
	multi_hit( ch, ech, TYPE_UNDEFINED );
	return TRUE;
    }

    return FALSE;
}



bool spec_janitor( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    OBJ_DATA *trash;
    OBJ_DATA *trash_next;

    if ( !IS_AWAKE(ch) )
	return FALSE;

    for ( trash = ch->in_room->first_content; trash; trash = trash_next )
    {
	trash_next = trash->next_content;
	if ( !BV_IS_SET( trash->wear_flags, ITEM_TAKE )
	||    IS_OBJ_STAT( trash, ITEM_BURRIED ) )
	    continue;
    if( IS_OBJ_STAT( trash, ITEM_PROTOTYPE ) && !BV_IS_SET( ch->act, ACT_PROTOTYPE ) )
        continue;        

    if ( trash->item_type == ITEM_DRINK_CON
	||   trash->item_type == ITEM_TRASH
	||   trash->cost < 10
	||  (trash->pIndexData->vnum == OBJ_VNUM_SHOPPING_BAG
	&&  !trash->first_content) )
	{
	    act( AT_ACTION, "$n picks up some trash.", ch, NULL, NULL, TO_ROOM );
	    obj_from_room( trash );
	    obj_to_char( trash, ch );
	    return TRUE;
	}
    }

    return FALSE;
}



bool spec_poison( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;

    if ( ch->position != POS_FIGHTING
    || ( victim = who_fighting( ch ) ) == NULL
    ||   number_percent( ) > 2 * ch->top_level )
	return FALSE;

    act( AT_HIT, "You bite $N!",  ch, NULL, victim, TO_CHAR    );
    act( AT_ACTION, "$n bites $N!",  ch, NULL, victim, TO_NOTVICT );
    act( AT_POISON, "$n bites you!", ch, NULL, victim, TO_VICT    );
    spell_poison( gsn_poison, ch->top_level, ch, victim );
    return TRUE;
}



bool spec_thief( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;
    int gold, maxgold;

    if ( ch->position != POS_STANDING )
	return FALSE;

    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;

	if ( victim == ch || get_trust(victim) >= LEVEL_IMMORTAL
	||   number_bits( 2 ) != 0
	||   !can_see( ch, victim ) )	/* Thx Glop */
	    continue;

	if ( IS_AWAKE(victim) && number_range( 0, ch->top_level ) == 0 )
	{
	    act( AT_ACTION, "You discover $n's hands in your wallet!",
		ch, NULL, victim, TO_VICT );
	    act( AT_ACTION, "$N discovers $n's hands in $S wallet!",
		ch, NULL, victim, TO_NOTVICT );
	    return TRUE;
	}
	else
	{
	    maxgold = 1000;
	    gold = victim->gold
	    	 * number_range( 1, URANGE(2, ch->top_level/4, 10) ) / 100;
	    ch->gold     += 9 * gold / 10;
	    victim->gold -= gold;
	    if ( ch->gold > maxgold )
	    {
		boost_economy( ch->in_room->area, ch->gold - maxgold/2 );
		ch->gold = maxgold/2;
	    }
	    return TRUE;
	}
    }

    return FALSE;
}

bool spec_auth( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;

    CHAR_DATA *victim;
    CHAR_DATA *v_next;
    char buf[MAX_STRING_LENGTH]; // Needed for do_fun command
    OBJ_INDEX_DATA *pObjIndex;
    OBJ_DATA *obj;
    bool hasdiploma;
    
    for ( victim = ch->in_room->first_person; victim; victim = v_next )
    {
	v_next = victim->next_in_room;
        
        if ( !IS_NPC(victim) && ( pObjIndex = get_obj_index( OBJ_VNUM_SCHOOL_DIPLOMA ) ) != NULL )
        {
             hasdiploma = FALSE;
             
             for ( obj = victim->last_carrying; obj; obj = obj->prev_content )
               if (obj->pIndexData == get_obj_index( OBJ_VNUM_SCHOOL_DIPLOMA ) )
                  hasdiploma = TRUE;
             
             if ( !hasdiploma )
             {                    
               obj = create_object( pObjIndex, 1 );
               obj = obj_to_char( obj, victim );
               send_to_char( "&cThe schoolmaster gives you a diploma, and shakes your hand.\n&w",victim);
             } 
        }
        
        if ( IS_NPC(victim)
            ||   !BV_IS_SET(victim->pcdata->flags, PCFLAG_UNAUTHED) || victim->pcdata->auth_state == 2 )
            continue;
        
        victim->pcdata->auth_state = 3;
        BV_REMOVE_BIT(victim->pcdata->flags, PCFLAG_UNAUTHED);
        if ( victim->pcdata->authed_by )
                    STRFREE( victim->pcdata->authed_by );
        victim->pcdata->authed_by = QUICKLINK( ch->name );
        SPRINTF( buf, "%s authorized %s", ch->name,
                        victim->name );
        to_channel( buf, CHANNEL_MONITOR, "Monitor", ch->top_level );
                                                                                                            
    
    }
    return FALSE;

}

/*
bool spec_random_pop( CHAR_DATA *ch )
{
    if ( !ch || !ch->in_room )
        return FALSE;


  int ran_array[MAX_RAN_POP] = { 309, 205, 10510, 28209, 29087, 26000, 1030, 5555,
  		      3026, 6230, 7340, 11215, 21050, 540, 28100, 28612,
  		    };
}
*/  

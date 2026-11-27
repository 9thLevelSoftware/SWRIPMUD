/***************************************************************************
*                           STAR WARS REALITY 1.0                          *
*--------------------------------------------------------------------------*
* Star Wars Reality Code Additions and changes from the Smaug Code         *
* copyright (c) 1997 by Sean Cooper                                        *
* -------------------------------------------------------------------------*
* Starwars and Starwars Names copyright(c) Lucasfilm Ltd.                  *
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
*     New Star Wars Skills Unit          *   
****************************************************************************/

#include <math.h> 
#include <sys/types.h>
#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "mud.h"

void    add_reinforcements  args( ( CHAR_DATA *ch ) );
ch_ret  one_hit             args( ( CHAR_DATA *ch, CHAR_DATA *victim, int dt ) );
int     xp_compute                ( CHAR_DATA *ch , CHAR_DATA *victim );
ROOM_INDEX_DATA *generate_exit( ROOM_INDEX_DATA *in_room, EXIT_DATA **pexit );
int ris_save( CHAR_DATA *ch, int chance, int ris );
CHAR_DATA *get_char_room_mp( CHAR_DATA *ch, const std::string& argument );
bool    autofly(SHIP_DATA *ship);
bool    aff_paralysis( CHAR_DATA *ch, CHAR_DATA *victim );

extern int      top_affect;

   

void do_improve_module( CHAR_DATA *ch, char *argument )
{
    std::string arg;
    int chance, origvalue;
    OBJ_DATA *obj;
    bool checktool;

    obj = ch->last_carrying; 

    CHECK_SUBRESTRICTED( ch );

    switch( ch->substate )
    { 
     default:
             arg = argument;
             
             if ( arg.empty() )
                {
                  send_to_char( "&RImprove what module?\n&w", ch);
                  return;   
                }

		checktool = FALSE;

                for ( obj = ch->last_carrying; obj; obj = obj->prev_content )     
                {
                  if (obj->item_type == ITEM_TOOLKIT)
                    checktool = TRUE;
                }
                
                if ( !checktool )
                {
                   send_to_char( "&RYou need a toolkit to improve a module.\n", ch );
                   return;
                }
             
                if ( ms_find_obj(ch) )
                      return;
                
                if ( ( obj = get_obj_carry( ch, arg ) ) == NULL )
                {
                        send_to_char( "&RYou do not have that item.\n&w", ch );
                        return;
                }                                                            
                
                if ( obj->item_type != ITEM_FIGHTERCOMP && obj->item_type != ITEM_MIDCOMP 
                  && obj->item_type != ITEM_CAPITALCOMP  )
                {
                       send_to_char( "&RYou can only improve ship modules.\n&w",ch);
                       return;
                }
                
                 chance = IS_NPC(ch) ? ch->top_level
                  : (int) (ch->pcdata->learned[gsn_shipmaintenance]);

               if ( number_percent( ) < chance )
               {
                 send_to_char( "&GYou begin the process of improving this module.\n", ch);
                 act( AT_PLAIN, "$n begins working on something.", ch,
                   NULL, arg , TO_ROOM );
                 add_timer ( ch , TIMER_DO_FUN , 50 , do_improve_module, 1 );
                 ch->dest_buf = str_dup(arg);
                 return;
               }
         send_to_char("&RYou can't determine what you should be doing.\n",ch);
         learn_from_failure( ch, gsn_shipmaintenance );
         return; 
     
     case 1:
      if ( !ch->dest_buf )
      {
         ch->substate = SUB_NONE;
         return;
      }
      arg = (const char*) ch->dest_buf;
      STR_DISPOSE( ch->dest_buf);
      break;
      
     case SUB_TIMER_DO_ABORT:
      if ( ch->dest_buf )
         STR_DISPOSE( ch->dest_buf );
      ch->substate = SUB_NONE;                                         
             send_to_char("&RYou are distracted and are unable to finish your work.\n&w", ch);
             return;
    }
    
    ch->substate = SUB_NONE;
    
    obj = get_obj_carry(ch, arg);
    if ( !obj || (obj->item_type != ITEM_FIGHTERCOMP && obj->item_type != ITEM_MIDCOMP
                    && obj->item_type != ITEM_CAPITALCOMP) )
    {
           send_to_char( "You seem to have lost the module you were working on!\n", ch );
           return;
    }                                                            
    
    separate_obj(obj);
    origvalue = obj->value[0];
    if ( origvalue < 100 )
    {
      obj->value[0] += 10;
      obj->value[0] = UMIN( obj->value[0], 100 );
    }
    else
    {
      obj->value[0] += 1;
      obj->value[0] = UMIN( obj->value[0], 105 );
    }
    
    send_to_char( "&GYou finish your work.\n", ch);
    act( AT_PLAIN, "$n finishes $s work.", ch,
         NULL, arg , TO_ROOM );
    
    {
    long xpgain;
         
    xpgain = UMIN( obj->cost*50 ,( exp_level(ch->skill_level[ENGINEERING_ABILITY]+1) - exp_level(ch->skill_level[ENGINEERING_ABILITY]) ) );
    gain_exp(ch, xpgain, ENGINEERING_ABILITY);
    ch_printf( ch , "You gain %ld engineering experience.", xpgain );
    }
                 
  learn_from_success( ch, gsn_shipmaintenance );
     
}


void do_reinforcements( CHAR_DATA *ch, char *argument )
{
   std::string arg;
   int chance, credits;
   
   if ( IS_NPC( ch ) || !ch->pcdata )
   return;
   
   CHECK_SUBRESTRICTED( ch );

   arg = argument;
   
   switch( ch->substate )
   { 
      default:
         if ( ch->backup_wait )
         {
            send_to_char( "&RYour reinforcements are already on the way.\n", ch );
            return;
         }
         
         if ( !ch->pcdata->clan )
         {
            send_to_char( "&RYou need to be a member of an organization before you can call for reinforcements.\n", ch );
            return;
         }    
         
         if ( ch->gold < ch->skill_level[LEADERSHIP_ABILITY] * 50 )
         {
            ch_printf( ch, "&RYou dont have enough credits to send for reinforcements.\n" );
            return;
         }    
         
         chance = (int) (ch->pcdata->learned[gsn_reinforcements]);
            if ( number_percent( ) < chance )
         {
            send_to_char( "&GYou begin making the call for reinforcements.\n", ch);
            act( AT_PLAIN, "$n begins issuing orders int $s comlink.", ch,
            NULL, arg , TO_ROOM );
            add_timer ( ch , TIMER_DO_FUN , 1 , do_reinforcements , 1 );
            ch->dest_buf = str_dup(arg);
            return;
         }
         send_to_char("&RYou call for reinforcements but nobody answers.\n",ch);
         learn_from_failure( ch, gsn_reinforcements );
         return; 
     
      case 1:
         if ( !ch->dest_buf )
         {
            ch->substate = SUB_NONE;
            return;
         }
         arg = (const char*) ch->dest_buf;
         STR_DISPOSE( ch->dest_buf);
         break;
      
      case SUB_TIMER_DO_ABORT:
         if ( ch->dest_buf )
            STR_DISPOSE( ch->dest_buf );
         ch->substate = SUB_NONE;                                         
               send_to_char("&RYou are interupted before you can finish your call.\n", ch);
               return;
   }
   
   ch->substate = SUB_NONE;

   if ( !ch->pcdata->clan )
   {
      send_to_char( "&RYou are no longer a member of an organization.\n", ch );
      return;
   }

   credits = ch->skill_level[LEADERSHIP_ABILITY] * 50;
   if ( ch->gold < credits )
   {
      send_to_char( "&RYou dont have enough credits to send for reinforcements.\n", ch );
      return;
   }

   send_to_char( "&GYour reinforcements are on the way.\n", ch);
   ch_printf( ch, "It cost you %d credits.\n", credits);
   ch->gold -= credits;
            
   learn_from_success( ch, gsn_reinforcements );
   
   if ( nifty_is_name( "empire" , ch->pcdata->clan->name ) )
      ch->backup_mob = MOB_VNUM_STORMTROOPER;
   else if ( nifty_is_name( "rebel" , ch->pcdata->clan->name ) )
      ch->backup_mob = MOB_VNUM_NR_TROOPER;
   else
      ch->backup_mob = MOB_VNUM_MERCINARY;

   ch->backup_wait = number_range(1,2);
    
}

void do_postguard( CHAR_DATA *ch, char *argument )
{
   std::string arg;
   int chance, credits;
   
   if ( IS_NPC( ch ) || !ch->pcdata )
   return;
   
   CHECK_SUBRESTRICTED( ch );

   arg = argument;

   switch( ch->substate )
   { 
      default:
         if ( ch->backup_wait )
         {
            send_to_char( "&RYou already have backup coming.\n", ch );
            return;
         }
         
         if ( !ch->pcdata->clan )
         {
            send_to_char( "&RYou need to be a member of an organization before you can call for a guard.\n", ch );
            return;
         }    
         
         if ( ch->gold < ch->skill_level[LEADERSHIP_ABILITY] * 30 )
         {
            send_to_char( "&RYou dont have enough credits.\n", ch );
            return;
         }    
         
         chance = (int) (ch->pcdata->learned[gsn_postguard]);
         if ( number_percent( ) < chance )
         {
            send_to_char( "&GYou begin making the call for reinforcements.\n", ch);
            act( AT_PLAIN, "$n begins issuing orders int $s comlink.", ch,
            NULL, arg , TO_ROOM );
            add_timer ( ch , TIMER_DO_FUN , 1 , do_postguard , 1 );
            ch->dest_buf = str_dup(arg);
            return;
         }
         send_to_char("&RYou call for a guard but nobody answers.\n",ch);
         learn_from_failure( ch, gsn_postguard );
         return; 
     
      case 1:
         if ( !ch->dest_buf )
         {
            ch->substate = SUB_NONE;
            return;
         }
         arg = (const char*) ch->dest_buf;
         STR_DISPOSE( ch->dest_buf);
         break;
      
      case SUB_TIMER_DO_ABORT:
         if ( ch->dest_buf )
            STR_DISPOSE( ch->dest_buf );
         ch->substate = SUB_NONE;                                         
         send_to_char("&RYou are interupted before you can finish your call.\n", ch);
         return;
   }
    
   ch->substate = SUB_NONE;

   if ( !ch->pcdata->clan )
   {
      send_to_char( "&RYou are no longer a member of an organization.\n", ch );
      return;
   }

   credits = ch->skill_level[LEADERSHIP_ABILITY] * 30;
   if ( ch->gold < credits )
   {
      send_to_char( "&RYou dont have enough credits.\n", ch );
      return;
   }
   
   send_to_char( "&GYour guard is on the way.\n", ch);
   ch_printf( ch, "It cost you %d credits.\n", credits);
   ch->gold -= credits;

   learn_from_success( ch, gsn_postguard );
   
   if ( nifty_is_name( "empire" , ch->pcdata->clan->name ) )
      ch->backup_mob = MOB_VNUM_IMP_GUARD;
   else if ( nifty_is_name( "rebel" , ch->pcdata->clan->name ) )
      ch->backup_mob = MOB_VNUM_NR_GUARD;
   else
      ch->backup_mob = MOB_VNUM_BOUNCER;

   ch->backup_wait = 1;

}

void add_reinforcements( CHAR_DATA *ch )
{
   MOB_INDEX_DATA  * pMobIndex;
   OBJ_DATA        * blaster;
   OBJ_INDEX_DATA  * pObjIndex;
//   char         buf[MAX_INPUT_LENGTH];
   int multiplier = 1;

   if ( !ch->in_room )
      return;

   if ( ( pMobIndex = get_mob_index( ch->backup_mob ) ) == NULL )
      return;

   log_printf("%s just posted a guard on %d.", ch->name ? ch->name : "(null)", ch->in_room->vnum );


   if ( ch->backup_mob == MOB_VNUM_STORMTROOPER ||
      ch->backup_mob == MOB_VNUM_NR_TROOPER   ||
      ch->backup_mob == MOB_VNUM_MERCINARY ||
      ch->backup_mob == MOB_VNUM_IMP_FORCES ||
      ch->backup_mob == MOB_VNUM_NR_FORCES   ||
      ch->backup_mob == MOB_VNUM_MERC_FORCES       )
   {
      CHAR_DATA * mob[3];
      int         mob_cnt;

      if ( ch->backup_mob == MOB_VNUM_IMP_FORCES ||
         ch->backup_mob == MOB_VNUM_NR_FORCES   ||
         ch->backup_mob == MOB_VNUM_MERC_FORCES )
      { multiplier = 2; }

      if (!ch->game)
      {
         send_to_char( "You must be in a game to call for reinforcements.\n", ch );            
         return;
      }
      
      send_to_char( "Your reinforcements have arrived.\n", ch );
      for ( mob_cnt = 0 ; mob_cnt < 3 ; mob_cnt++ )
      {
         int ability;
         mob[mob_cnt] = create_mobile( ch->game, pMobIndex );
         char_to_room( mob[mob_cnt], ch->in_room );
         act( AT_IMMORT, "$N has arrived.", ch, NULL, mob[mob_cnt], TO_ROOM );
         mob[mob_cnt]->top_level = (int) ( multiplier / 1.4 * ch->skill_level[LEADERSHIP_ABILITY]/3 );
         for ( ability = 0 ; ability < MAX_ABILITY ; ability++ )
            mob[mob_cnt]->skill_level[ability] = mob[mob_cnt]->top_level;
         mob[mob_cnt]->hit = mob[mob_cnt]->top_level*15;
         mob[mob_cnt]->max_hit = mob[mob_cnt]->hit;
         mob[mob_cnt]->armor = (int) ( 100- mob[mob_cnt]->top_level*2.5 );
         mob[mob_cnt]->damroll = mob[mob_cnt]->top_level/5;
         mob[mob_cnt]->hitroll = mob[mob_cnt]->top_level/5;
         if ( ( pObjIndex = get_obj_index( OBJ_VNUM_BLASTECH_E11 ) ) != NULL )
         {
            blaster = create_object( pObjIndex, mob[mob_cnt]->top_level );
            obj_to_char( blaster, mob[mob_cnt] );
            equip_char( mob[mob_cnt], blaster, WEAR_WIELD );
         } 
         if ( mob[mob_cnt]->master )
            stop_follower( mob[mob_cnt] );
         add_follower( mob[mob_cnt], ch );
         BV_SET_BIT( mob[mob_cnt]->affected_by, AFF_CHARM );
         do_setblaster( mob[mob_cnt] , "full" );
      }
   }
   else
   {
      CHAR_DATA *mob;
      int ability;

      if ( ch->backup_mob == MOB_VNUM_IMP_ELITE ||
         ch->backup_mob == MOB_VNUM_NR_ELITE   ||
         ch->backup_mob == MOB_VNUM_MERC_ELITE )
      { multiplier = 2; }

      if (!ch->game)
      {
         send_to_char( "You must be in a game to call for reinforcements.\n", ch );            
         return;
      }

      mob = create_mobile( ch->game, pMobIndex );
      char_to_room( mob, ch->in_room );
      if ( ch->pcdata && ch->pcdata->clan )
      {
         std::string tmpbuf;

         STRFREE( mob->name );
         mob->name = STRALLOC( ch->pcdata->clan->name );
         tmpbuf = "(" + std::string(ch->pcdata->clan->name) + ") " + mob->long_descr;
         STRFREE( mob->long_descr );
         mob->long_descr = STRALLOC( tmpbuf );
      }
      act( AT_IMMORT, "$N has arrived.", ch, NULL, mob, TO_ROOM );
      send_to_char( "Your guard has arrived.\n", ch );
      mob->top_level = multiplier * ch->skill_level[LEADERSHIP_ABILITY] / 2;
      for ( ability = 0 ; ability < MAX_ABILITY ; ability++ )
         mob->skill_level[ability] = mob->top_level;
      mob->hit = mob->top_level*10;
      mob->max_hit = mob->hit;
      mob->armor = (int) ( 100- mob->top_level*2.5 );
      mob->damroll = mob->top_level/5;
      mob->hitroll = mob->top_level/5;
      if ( ( pObjIndex = get_obj_index( OBJ_VNUM_BLASTECH_E11 ) ) != NULL )
      {
         blaster = create_object( pObjIndex, mob->top_level );
         obj_to_char( blaster, mob );
         equip_char( mob, blaster, WEAR_WIELD );
      }

      /* for making this more accurate in the future */

      if ( mob->mob_clan )
         STRFREE ( mob->mob_clan );
      if ( ch->pcdata && ch->pcdata->clan )
         mob->mob_clan = STRALLOC( ch->pcdata->clan->name );
   }                    
}

void do_torture( CHAR_DATA *ch, char *argument )
{
   std::string arg;
   CHAR_DATA *victim;
   int chance, dam;
   bool fail;
   
   if ( !IS_NPC(ch)
   &&  ch->pcdata->learned[gsn_torture] <= 0  )
   {
      send_to_char("Your mind races as you realize you have no idea how to do that.\n", ch );
      return;
   }

   if ( IS_NPC(ch) && IS_AFFECTED( ch, AFF_CHARM ) )
   {
      send_to_char( "You can't do that right now.\n", ch );
      return;
   }

   one_argument( argument, arg );

   if ( ch->mount )
   {
      send_to_char( "You can't get close enough while mounted.\n", ch );
      return;
   }

   if ( arg.empty() )
   {
      send_to_char( "Torture whom?\n", ch );
      return;
   }

   if ( ( victim = get_char_room( ch, arg ) ) == NULL )
   {
      send_to_char( "They aren't here.\n", ch );
      return;
   }

   if ( victim == ch )
   {
      send_to_char( "Are you masacistic or what...\n", ch );
      return;
   }
   
   if ( !IS_AWAKE(victim) )
   {
      send_to_char( "You need to wake them first.\n", ch );
      return;
   }

   if ( is_safe( ch, victim ) )
      return;

   if ( victim->fighting )
   {
      send_to_char( "You can't torture someone whos in combat.\n", ch );
      return;
   }
   
   ch->alignment -= 100;
   ch->alignment = URANGE( -1000, ch->alignment, 1000 );
   gmcp_evt_char_stats(ch);    
   WAIT_STATE( ch, skill_table[gsn_torture]->beats );
   
   fail = FALSE;
   chance = ris_save( victim, (ch->skill_level[LEADERSHIP_ABILITY])/10, RIS_PARALYSIS );
   if ( chance == 1000 )
      fail = TRUE;
   else
      fail = saves_para_petri( chance, victim );

   if ( !IS_NPC(ch) && !IS_NPC(victim) )
      chance = ch->game->get_sysdata()->stun_plr_vs_plr;
   else
      chance = ch->game->get_sysdata()->stun_regular;
   if ( !fail
   && (  IS_NPC(ch)
   || (number_percent( ) + chance) < ch->pcdata->learned[gsn_torture] ) )
   {
      learn_from_success( ch, gsn_torture );
      WAIT_STATE( ch,     2 * PULSE_VIOLENCE );
      WAIT_STATE( victim, PULSE_VIOLENCE );
      act( AT_SKILL, "$N slowly tortures you. The pain is excruciating.", victim, NULL, ch, TO_CHAR );
      act( AT_SKILL, "You torture $N, leaving $M screaming in pain.", ch, NULL, victim, TO_CHAR );
      act( AT_SKILL, "$n tortures $N, leaving $M screaming in agony!", ch, NULL, victim, TO_NOTVICT );
        
      dam = dice(UMAX(1, ch->skill_level[LEADERSHIP_ABILITY] / 80), 4);
      const int limit = UMAX(0, UMIN(victim->max_hit - 10, victim->hit - 1));
      dam = UMIN(dam, limit);
      victim->hit -= dam;
      victim->max_hit -= dam;
      update_pos(victim);
      gmcp_evt_char_vitals(victim);
      
      ch_printf(victim, "You lose %d permanent hit points.\n", dam);
      ch_printf(ch, "They lose %d permanent hit points.\n", dam);
      
   }
   else
   {
      act( AT_SKILL, "$N tries to cut off your finger!", victim, NULL, ch, TO_CHAR );
      act( AT_SKILL, "You mess up big time.", ch, NULL, victim, TO_CHAR );
      act( AT_SKILL, "$n tries to painfully torture $N.", ch, NULL, victim, TO_NOTVICT );
      WAIT_STATE( ch,     2 * PULSE_VIOLENCE );
      global_retcode = multi_hit( victim, ch, TYPE_UNDEFINED );
   }
   return;
    
}

void do_disguise( CHAR_DATA *ch, char *argument )
{
   std::string argstr = argument;
   int chance;
   sh_int gsn;

   if ( IS_NPC(ch) )
   return;

   if ( BV_IS_SET( ch->pcdata->flags, PCFLAG_NOTITLE ))
   {
      send_to_char( "You try but the Force resists you.\n", ch );
      return;
   }

   if(ch->pcdata->learned[gsn_disguise] <= 0)
   gsn = gsn_cloak;
   else
   gsn = gsn_disguise;

   if ( argstr.empty() )
   {
   send_to_char( "Change your title to what?\n", ch );
   return;
   }

   chance = (int) (ch->pcdata->learned[gsn]);

   if ( number_percent( ) > chance )
   {
      send_to_char( "You try to disguise yourself but fail.\n", ch );
      return;
   }

   if ( argstr.length() > 50 )
      argstr = argstr.substr(0, 50);

   smash_tilde( argstr );
   set_title( ch, argstr );
   learn_from_success( ch, gsn );
   gmcp_evt_char_status(ch);
   send_to_char( "Ok.\n", ch );
}

void do_mine( CHAR_DATA *ch, char *argument )
{
   std::string arg;
   OBJ_DATA *obj;
   bool shovel;
   sh_int move;

   if ( IS_NPC(ch) || !ch->pcdata || ch->pcdata->learned[gsn_mine] <= 0 )
   {
      ch_printf( ch, "You have no idea how to do that.\n" );
      return;
   }

   if ( !ch->in_room )
      return;

    one_argument( argument, arg );

   if ( arg.empty() )
   {
      send_to_char( "And what will you mine the room with?\n", ch );
      return;
   }

   if ( ms_find_obj(ch) )
      return;

   shovel = FALSE;
   for ( obj = ch->first_carrying; obj; obj = obj->next_content )
      if ( obj->item_type == ITEM_SHOVEL )
      {
         shovel = TRUE;
         break;
      }

   obj = get_obj_list_rev( ch, arg, ch->in_room->last_content );
   if ( !obj )
   {
      send_to_char( "You don't see one here.\n", ch );
      return;
   }

   separate_obj(obj);
   if ( obj->item_type != ITEM_LANDMINE )
   {
      act( AT_PLAIN, "That's not a landmine!", ch, obj, 0, TO_CHAR );
      return;
   }

   if ( !CAN_WEAR(obj, ITEM_TAKE) )
   {
      act( AT_PLAIN, "You cannot bury $p.", ch, obj, 0, TO_CHAR );
      return;
   }

   switch( ch->in_room->sector_type )
   {
      case SECT_CITY:
      case SECT_INSIDE:
         send_to_char( "The floor is too hard to dig through.\n", ch );
         return;
      case SECT_WATER_SWIM:
      case SECT_WATER_NOSWIM:
      case SECT_UNDERWATER:
         send_to_char( "You cannot bury a mine in the water.\n", ch );
         return;
      case SECT_AIR:
         send_to_char( "What?  Bury a mine in the air?!\n", ch );
         return;
   }

   if ( obj->weight > (UMAX(5, (can_carry_w(ch) / 10)))
   &&  !shovel )
   {
      send_to_char( "You'd need a shovel to bury something that big.\n", ch );
      return;
   }

   move = (obj->weight * 50 * (shovel ? 1 : 5)) / UMAX(1, can_carry_w(ch));
   move = URANGE( 2, move, 1000 );
   if ( move > ch->move )
   {
      send_to_char( "You don't have the energy to bury something of that size.\n", ch );
      return;
   }
   ch->move -= move;

   BV_SET_BIT( obj->objflags, ITEM_BURRIED );
   WAIT_STATE( ch, URANGE( 10, move / 2, 100 ) );

   STRFREE ( obj->armed_by );
   obj->armed_by = STRALLOC ( ch->name );
   gmcp_evt_char_vitals(ch);
   ch_printf( ch, "You arm and bury %s.\n", obj->short_descr );
   act( AT_PLAIN, "$n arms and buries $p.", ch, obj, NULL, TO_ROOM );

   learn_from_success( ch, gsn_mine );

   return;
}

void do_first_aid( CHAR_DATA *ch, char *argument )
{
   OBJ_DATA   *medpac;
   CHAR_DATA  *victim;
   int         heal;
   std::string buf;

   if ( IS_NPC(ch) || !ch->pcdata )
   {
      send_to_char( "You have no idea how to do that.\n", ch );
      return;
   }

   if ( !ch->in_room )
      return;

   if ( ch->position == POS_FIGHTING )
   {
         send_to_char( "You can't do that while fighting!\n",ch );
         return;
   }
   
   medpac = get_eq_char( ch, WEAR_HOLD );
   if ( !medpac || medpac->item_type != ITEM_MEDPAC )
   {
         send_to_char( "You need to be holding a medpac.\n",ch );
         return;
   }  

   if ( medpac->value[0] <= 0 )
   {
         send_to_char( "Your medpac seems to be empty.\n",ch );
         return;         
   }
   
   if ( argument[0] == '\0' )
      victim = ch;
   else
      victim = get_char_room( ch, argument );
            
   if ( !victim )
   {
       ch_printf( ch, "I don't see any %s here...\n" , argument );     
       return;
   }
   
   heal = number_range( 1, 150 );
   
   if ( heal > ch->pcdata->learned[gsn_first_aid]*2 )
   {
       ch_printf( ch, "You fail in your attempt at first aid.\n");     
       learn_from_failure( ch , gsn_first_aid );
       return;
   } 
      
   if ( victim == ch )
   {
       ch_printf( ch, "You tend to your wounds.\n");
       buf = "$n uses " + std::string(medpac->short_descr) + " to help heal $s wounds.";
       act( AT_ACTION, buf, ch, NULL, victim, TO_ROOM );  
   }
   else
   {
       buf = "You tend to $N's wounds.";        
       act( AT_ACTION, buf, ch, NULL, victim, TO_CHAR );  
       buf = "$n uses " + std::string(medpac->short_descr) + " to help heal $N's wounds.";        
       act( AT_ACTION, buf, ch, NULL, victim, TO_NOTVICT );  
       buf = "$n uses " + std::string(medpac->short_descr) + " to help heal your wounds.";        
       act( AT_ACTION, buf, ch, NULL, victim, TO_VICT );  
   }

   --medpac->value[0];
   victim->hit += URANGE ( 0, heal , victim->max_hit - victim->hit );

   learn_from_success( ch , gsn_first_aid );
}

void do_snipe( CHAR_DATA *ch, char *argument )
{
   OBJ_DATA        * wield;
   std::string       arg;  
   std::string       arg2;
   std::string       argstr = argument;
   sh_int            dir, orig_dir, dist;
   sh_int            max_dist = 1;
   EXIT_DATA       * pexit;
   ROOM_INDEX_DATA * was_in_room;
   ROOM_INDEX_DATA * to_room;
   CHAR_DATA       * victim = NULL;
   int               chance;
   std::string       buf;
   bool              pfound = FALSE;

   if ( !ch->in_room )
      return;
   
   if ( BV_IS_SET( ch->in_room->room_flags, ROOM_SAFE ) )
   {
      set_char_color( AT_MAGIC, ch );
      send_to_char( "You'll have to do that elswhere.\n", ch );
      return;
   }  
     
   if ( get_eq_char( ch, WEAR_DUAL_WIELD ) != NULL )
   {
      send_to_char( "You can't do that while wielding two weapons.",ch );
      return;
   }
    
   wield = get_eq_char( ch, WEAR_WIELD );
   if ( !wield || wield->item_type != ITEM_WEAPON || (!BV_IS_SET(wield->objflags,WEAPON_BLASTER) && !BV_IS_SET(wield->objflags,WEAPON_BOWCASTER) ) )
   {

      send_to_char( "You don't seem to be holding a blaster",ch );
      return;
   }  

   if ( !IS_NPC(ch) && ch->pcdata && ch->pcdata->learned[gsn_snipe]> 100)
      max_dist += (ch->pcdata->learned[gsn_snipe]) / 15;
     
   argstr = one_argument( argstr, arg );
   argstr = one_argument( argstr, arg2 );
   
   if ( ( dir = get_door( arg ) ) == -1 || arg2.empty() )
   {
      send_to_char( "Usage: snipe <dir> <target>\n", ch );
      return;
   }
   orig_dir = dir;
 
   if ( ( pexit = get_exit( ch->in_room, dir ) ) == NULL )
   {
      send_to_char( "Are you expecting to fire through a wall!?\n", ch );
      return;
   }

   if ( IS_SET( pexit->exit_info, EX_CLOSED ) )
   {
      send_to_char( "Are you expecting to fire through a door!?\n", ch );
      return;
   }

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
     

      if ( IS_NPC(ch) && ( victim = get_char_room_mp( ch, arg2 ) ) != NULL )
      {
         pfound = TRUE;
         break;
      }
      else if ( !IS_NPC(ch) && ( victim = get_char_room( ch, arg2 ) ) != NULL )
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
   {
      ch_printf( ch, "You don't see that person to the %s!\n", dir_name[orig_dir] );
      return;
   }
   
   if ( victim == ch )
   {
   send_to_char( "Shoot yourself ... really?\n", ch );
   return;
   }

   if ( !victim->in_room )
      return;
    
   if ( BV_IS_SET( victim->in_room->room_flags, ROOM_SAFE ) )
   {
   set_char_color( AT_MAGIC, ch );
   send_to_char( "You can't shoot them there.\n", ch );
   return;
   }
 
   if ( is_safe( ch, victim ) )
   return;
    
   if ( IS_AFFECTED(ch, AFF_CHARM) && ch->master == victim )
   {
         act( AT_PLAIN, "$N is your beloved master.", ch, NULL, victim, TO_CHAR );
         return;
   }

   if ( ch->position == POS_FIGHTING )
   {
      send_to_char( "You do the best you can!\n", ch );
      return;
   }
    
   if ( !IS_NPC( victim ) && BV_IS_SET( ch->act, PLR_NICE ) )
   {
      send_to_char( "You feel too nice to do that!\n", ch );
      return;
   }
    
   chance = IS_NPC(ch) ? 100
         : (int)  (ch->pcdata->learned[gsn_snipe]) ; 
   
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
      case DIR_SOMEWHERE:
         break;
   }
    
   char_from_room( ch );
   char_to_room( ch, victim->in_room );    
               
   if( number_percent(  ) < chance )
   {
      char_from_room( ch );
      char_to_room( ch, was_in_room );
      buf = str_printf("$n fires a blaster shot to the %s.", dir_name[orig_dir]);
      act( AT_ACTION, buf.c_str(), ch, NULL, NULL, TO_ROOM );

      if ( char_died(victim) || !victim->in_room )
         return;

      char_from_room( ch );
      char_to_room( ch, victim->in_room );

      buf = str_printf("A blaster shot fires at you from the %s.", dir_name[dir]);
      act( AT_ACTION, buf, victim, NULL, ch, TO_CHAR );
      act( AT_ACTION, "You fire at $N.", ch, NULL, victim, TO_CHAR );
      buf = str_printf("A blaster shot fires at $N from the %s.", dir_name[dir]);
      act( AT_ACTION, buf, ch, NULL, victim, TO_NOTVICT );

      one_hit( ch, victim, TYPE_UNDEFINED );

      if( char_died( ch ) )
         return;

      stop_fighting( ch, TRUE );

      learn_from_success( ch, gsn_snipe );
   }
   else
   {
      char_from_room( ch );
      char_to_room( ch, was_in_room );
      buf = str_printf("$n fires a blaster shot to the %s.", dir_name[orig_dir]);
      act( AT_ACTION, buf.c_str(), ch, NULL, NULL, TO_ROOM );

      if ( char_died(victim) || !victim->in_room )
         return;

      char_from_room( ch );
      char_to_room( ch, victim->in_room );

      act( AT_ACTION, "You fire at $N but don't even come close.", ch, NULL, victim, TO_CHAR );
      buf = str_printf("A blaster shot fired from the %s barely misses you.", dir_name[dir]);
      act( AT_ACTION, buf, ch, NULL, victim, TO_ROOM );
      learn_from_failure( ch, gsn_snipe );
   }
   if ( !char_died(ch) && ch->in_room != was_in_room && was_in_room )
   {
      if ( ch->in_room )
         char_from_room( ch );
      char_to_room( ch, was_in_room );
   }
     
   if ( IS_NPC(ch) )                               
      WAIT_STATE( ch, 1 * PULSE_VIOLENCE );
   else
   {
      if ( number_percent() < ch->pcdata->learned[gsn_third_attack] )
            WAIT_STATE( ch, 1 * PULSE_PER_SECOND );
      else if ( number_percent() < ch->pcdata->learned[gsn_second_attack] )
            WAIT_STATE( ch, 2 * PULSE_PER_SECOND );
      else 
            WAIT_STATE( ch, 3 * PULSE_PER_SECOND );
   }
   if ( !char_died(ch) && victim && IS_NPC( victim ) && !char_died(victim) )
   {
      if ( BV_IS_SET( victim->act , ACT_SENTINEL ) )
      {
         victim->was_sentinel = victim->in_room;
         BV_REMOVE_BIT( victim->act, ACT_SENTINEL );
      }
      
      start_hating( victim , ch );
      start_hunting( victim, ch );
     
   } 
   
}

/* syntax throw <obj> [direction] [target] */

void do_throw( CHAR_DATA *ch, char *argument )
{
   OBJ_DATA        * obj = NULL;
   OBJ_DATA        * tmpobj;
   std::string  arg;  
   std::string  arg2;
   std::string  arg3;
   std::string argstr = argument;
   sh_int            dir = 0, orig_dir = 0;
   EXIT_DATA       * pexit;
   ROOM_INDEX_DATA * was_in_room;
   ROOM_INDEX_DATA * to_room;
   CHAR_DATA       * victim;
   std::string       buf;

   if ( !ch->in_room )
      return;

   argstr = one_argument( argstr, arg );
   argstr = one_argument( argstr, arg2 );
   argstr = one_argument( argstr, arg3 );
   
   was_in_room = ch->in_room;
      
   if ( arg.empty() )
   {
     send_to_char( "Usage: throw <object> [direction] [target]\n", ch );
     return;
   }

   {
      static const int throw_locs[] = { WEAR_MISSILE_WIELD, WEAR_HOLD, WEAR_WIELD, WEAR_DUAL_WIELD };
      for ( int i = 0; i < 4; ++i )
      {
         OBJ_DATA *cand = get_eq_char( ch, throw_locs[i] );
         if ( cand && nifty_is_name( arg, cand->name ) )
         {
            obj = cand;
            break;
         }
      }
      if ( !obj )
      {
         for ( int i = 0; i < 4; ++i )
         {
            OBJ_DATA *cand = get_eq_char( ch, throw_locs[i] );
            if ( cand && nifty_is_name_prefix( arg, cand->name ) )
            {
               obj = cand;
               break;
            }
         }
      }
   }
   if ( !obj )
   {
         ch_printf( ch, "You don't seem to be holding or wielding %s.\n", arg.c_str() );
         return;
   }  

   if ( IS_OBJ_STAT(obj, ITEM_NOREMOVE) )
   {
      act( AT_PLAIN, "You can't throw $p.", ch, obj, NULL, TO_CHAR );
      return;
   }

   if ( ch->position == POS_FIGHTING )
   {
      victim = who_fighting( ch );
      if ( !victim || char_died ( victim ) )
         return;
      act( AT_ACTION, "You throw $p at $N.", ch, obj, victim, TO_CHAR );
      act( AT_ACTION, "$n throws $p at $N.", ch, obj, victim, TO_NOTVICT );
      act( AT_ACTION, "$n throw $p at you.", ch, obj, victim, TO_VICT );        
   }
   else if ( arg2.empty() )
   {
      buf = "$n throws " + std::string(obj->short_descr) + " at the floor.";
      act( AT_ACTION, buf.c_str(), ch, NULL, NULL, TO_ROOM );  
      ch_printf( ch, "You throw %s at the floor.\n", obj->short_descr );
      
      victim = NULL;
   }
   else  if ( ( dir = get_door( arg2 ) ) != -1 )
   {
      orig_dir = dir;
      if ( ( pexit = get_exit( ch->in_room, dir ) ) == NULL )
      {
         send_to_char( "Are you expecting to throw it through a wall!?\n", ch );
         return;
      }

      
      if ( IS_SET( pexit->exit_info, EX_CLOSED ) )
      {
          send_to_char( "Are you expecting to throw it  through a door!?\n", ch );
          return;
      }
      
      
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
        case DIR_SOMEWHERE:
           break;
      }

      to_room = NULL;
      if ( pexit->distance > 1 )
         to_room = generate_exit( ch->in_room , &pexit );
    
      if ( to_room == NULL )
         to_room = pexit->to_room;
      if ( !to_room )
         return;
    

      char_from_room( ch );
      char_to_room( ch, to_room );    
     
      victim = get_char_room( ch, arg3 );

      if ( victim )
      { 
         if ( is_safe( ch, victim ) )
         {
            char_from_room( ch );
            char_to_room( ch, was_in_room );
            return;
         }
    
         if ( IS_AFFECTED(ch, AFF_CHARM) && ch->master == victim )
         {
            act( AT_PLAIN, "$N is your beloved master.", ch, NULL, victim, TO_CHAR );
            char_from_room( ch );
            char_to_room( ch, was_in_room );
            return;
         }
      
         if ( !IS_NPC( victim ) && BV_IS_SET( ch->act, PLR_NICE ) )
         {
            send_to_char( "You feel too nice to do that!\n", ch );
            char_from_room( ch );
            char_to_room( ch, was_in_room );
            return;
         }
      
         char_from_room( ch );
         char_to_room( ch, was_in_room );    

         
         if ( BV_IS_SET( ch->in_room->room_flags, ROOM_SAFE ) )
         {
               set_char_color( AT_MAGIC, ch );
               send_to_char( "You'll have to do that elswhere.\n", ch );
               return;
         }
         
         to_room = NULL;
         if ( pexit->distance > 1 )
            to_room = generate_exit( ch->in_room , &pexit );
      
         if ( to_room == NULL )
            to_room = pexit->to_room;
         if ( !to_room )
            return;
      
       
         char_from_room( ch );
         char_to_room( ch, to_room );

         buf = "Someone throws " + std::string(obj->short_descr) + " at you from the " + dir_name[dir] + ".";
         act( AT_ACTION, buf, victim, NULL, ch, TO_CHAR );
         act( AT_ACTION, "You throw $p at $N.", ch, obj, victim, TO_CHAR );
         char_from_room( ch );
         char_to_room( ch, was_in_room );
         buf = "$n throws " + std::string(obj->short_descr) + " to the " + dir_name[orig_dir] + ".";
         act( AT_ACTION, buf.c_str(), ch, NULL, NULL, TO_ROOM );
         char_from_room( ch );
         char_to_room( ch, to_room );
         buf = std::string(obj->short_descr) + " is thrown at $N from the " + dir_name[dir] + ".";
         act( AT_ACTION, buf.c_str(), ch, NULL, victim, TO_NOTVICT );
      }
      else
      {
         ch_printf( ch, "You throw %s %s.\n", obj->short_descr, dir_name[orig_dir] );
         char_from_room( ch );
         char_to_room( ch, was_in_room );
         buf = "$n throws " + std::string(obj->short_descr) + " to the " + dir_name[orig_dir] + ".";
         act( AT_ACTION, buf.c_str(), ch, NULL, NULL, TO_ROOM );
         char_from_room( ch );
         char_to_room( ch, to_room );
         buf = std::string(obj->short_descr) + " is thrown from the " + dir_name[dir] + ".";
         act( AT_ACTION, buf.c_str(), ch, NULL, NULL, TO_ROOM );
      }
   }
   else if ( ( victim = get_char_room( ch, arg2 ) ) != NULL )
   {
      if ( is_safe( ch, victim ) )
         return;
   
      if ( IS_AFFECTED(ch, AFF_CHARM) && ch->master == victim )
      {
         act( AT_PLAIN, "$N is your beloved master.", ch, NULL, victim, TO_CHAR );
         return;
      }
   
      if ( !IS_NPC( victim ) && BV_IS_SET( ch->act, PLR_NICE ) )
      {
         send_to_char( "You feel too nice to do that!\n", ch );
         return;
      }

   }
   else
   {
       ch_printf( ch, "They don't seem to be here!\n");
       return;         
   }

   
   if ( obj == get_eq_char( ch, WEAR_WIELD )
   && ( tmpobj = get_eq_char( ch, WEAR_DUAL_WIELD)) != NULL )
       tmpobj->wear_loc = WEAR_WIELD;

   unequip_char( ch, obj );
   separate_obj( obj );
   int obj_weight = obj->weight;
   obj_from_char( obj );
   if ( ch->in_room )
      obj = obj_to_room( obj, ch->in_room );
   else
   {
      extract_obj( obj );
      obj = NULL;
   }
   
   if ( obj && obj->item_type != ITEM_GRENADE )
       damage_obj ( obj );
   
   if ( !char_died(ch) && ch->in_room != was_in_room && was_in_room )
   {
      if ( ch->in_room )
         char_from_room( ch );
      char_to_room( ch, was_in_room );    
   }
   
   if ( !victim || char_died( victim ) )
      learn_from_failure( ch, gsn_throw );
   else
   {

      WAIT_STATE( ch, skill_table[gsn_throw]->beats );
      if ( IS_NPC(ch) || number_percent( ) < ch->pcdata->learned[gsn_throw] )
      {
         learn_from_success( ch, gsn_throw );
         global_retcode = damage( ch, victim, number_range( obj_weight*2 , (obj_weight*2 + ch->perm_str) ), TYPE_HIT );
      }
      else
      {
         learn_from_failure( ch, gsn_throw );
         global_retcode = damage( ch, victim, 0, TYPE_HIT );
      }
   
      if ( !char_died( ch ) && IS_NPC( victim ) && !char_died ( victim) )
      {
         if ( BV_IS_SET( victim->act , ACT_SENTINEL ) )
         {
            victim->was_sentinel = victim->in_room;
            BV_REMOVE_BIT( victim->act, ACT_SENTINEL );
         }
   
         start_hating( victim , ch );
         start_hunting( victim, ch );
   
      } 

   }
   
   return;                                   
  
}

void do_beg( CHAR_DATA *ch, char *argument )
{
   std::string buf;
   std::string arg1;
   CHAR_DATA *victim;
   int percent, xp;
   int amount;

   if ( IS_NPC (ch) || !ch->in_room ) return;
   
   one_argument( argument, arg1 );

   if ( ch->mount )
   {
   send_to_char( "You can't do that while mounted.\n", ch );
   return;
   }

   if ( arg1.empty() )
   {
   send_to_char( "Beg fo money from whom?\n", ch );
   return;
   }

   if ( ms_find_obj(ch) )
      return;

   if ( ( victim = get_char_room( ch, arg1 ) ) == NULL )
   {
      send_to_char( "They aren't here.\n", ch );
      return;
   }

   if ( victim == ch )
   {
      send_to_char( "That's pointless.\n", ch );
      return;
   }

   if ( BV_IS_SET( ch->in_room->room_flags, ROOM_SAFE ) )
   {
      set_char_color( AT_MAGIC, ch );
      send_to_char( "This isn't a good place to do that.\n", ch );
      return;
   }

   if ( ch->position == POS_FIGHTING )
   {
      send_to_char( "Interesting combat technique.\n" , ch );
      return;
   }
   
   if ( victim->position == POS_FIGHTING )
   {
      send_to_char( "They're a little busy right now.\n" , ch );
      return;
   }
   
   if ( ch->position <= POS_SLEEPING )
   {
      send_to_char( "In your dreams or what?\n" , ch );
      return;
   }
   
   if ( victim->position <= POS_SLEEPING )
   {
      send_to_char( "You might want to wake them first...\n" , ch );
      return;
   }

   if ( !IS_NPC( victim ) )
   {
      send_to_char( "You beg them for money.\n", ch );
      act( AT_ACTION, "$n begs you to give $s some change.\n", ch, NULL, victim, TO_VICT    );
      act( AT_ACTION, "$n begs $N for change.\n",  ch, NULL, victim, TO_NOTVICT );
      return;
   }
   
   WAIT_STATE( ch, skill_table[gsn_beg]->beats );
   percent  = number_percent( ) + ch->skill_level[SMUGGLING_ABILITY] + victim->top_level;

   if ( percent > ch->pcdata->learned[gsn_beg]  ) 
   {
      /*
      * Failure.
      */
      send_to_char( "You beg them for money but don't get any!\n", ch );
      act( AT_ACTION, "$n is really getting on your nerves with all this begging!\n", ch, NULL, victim, TO_VICT    );
      act( AT_ACTION, "$n begs $N for money.\n",  ch, NULL, victim, TO_NOTVICT );

            if ( victim->alignment < 0 && victim->top_level >= ch->top_level+5 )
      {
      buf = str_printf("%s is an annoying beggar and needs to be taught a lesson!", ch->name);
      do_yell( victim, (char*)buf.c_str() );
               global_retcode = multi_hit( victim, ch, TYPE_UNDEFINED );
      }

      learn_from_failure( ch, gsn_beg );

      return;
   }

   
   act( AT_ACTION, "$n begs $N for money.\n",  ch, NULL, victim, TO_NOTVICT );
   act( AT_ACTION, "$n begs you for money!\n", ch, NULL, victim, TO_VICT    );

   amount = UMIN( victim->gold , number_range(1, 10) );
   if ( amount <= 0 )
   {
      do_look( victim , ch->name );
      do_say(victim, "Sorry I have nothing to spare.");
      learn_from_failure( ch, gsn_beg );
      return;
   }

   ch->gold     += amount;
   victim->gold -= amount;
   ch_printf( ch, "%s gives you %d credits.\n", victim->short_descr , amount );
   learn_from_success( ch, gsn_beg );
   xp = UMIN( amount*10 , ( exp_level( ch->skill_level[SMUGGLING_ABILITY]+1) - exp_level( ch->skill_level[SMUGGLING_ABILITY])  )  );    
   xp = UMIN( xp , xp_compute( ch, victim ) );
   gain_exp( ch, xp, SMUGGLING_ABILITY );  
   ch_printf( ch, "&WYou gain %d smuggling experience points!\n", xp );
   act( AT_ACTION, "$N gives $n some money.\n",  ch, NULL, victim, TO_NOTVICT );
   act( AT_ACTION, "You give $n some money.\n", ch, NULL, victim, TO_VICT    );

   return;

}

void do_pickshiplock( CHAR_DATA *ch, char *argument )
{
   do_pick( ch, argument );
}

void do_hijack( CHAR_DATA *ch, char *argument )
{
   int chance; 
   SHIP_DATA *ship;
   std::string buf2;
   CHAR_DATA *p, *p_prev, *victim;


   if ( !ch->in_room )
      return;

   if ( (ship = ship_from_cockpit(ch->game, ch->in_room->vnum)) == NULL )
   {
      send_to_char("&RYou must be in the cockpit of a ship to do that!\n",ch);
      return;
   }

   if ( ship->shipclass > SHIP_PLATFORM )
   {
      send_to_char("&RThis isn't a spacecraft!\n",ch);
      return;
   }
   
   if ( (ship = ship_from_pilotseat(ch->game, ch->in_room->vnum)) == NULL )  
   {
      send_to_char("&RYou don't seem to be in the pilot seat!\n",ch);
      return;
   }
   
   if ( check_pilot( ch , ship ) )
   {
      send_to_char("&RWhat would be the point of that!\n",ch);
      return;
   }

   if ( ship->type == MOB_SHIP && get_trust(ch) < 102 )
   {
      send_to_char("&RThis ship isn't pilotable by mortals at this point in time...\n",ch);
      return;
   }
   
   if  ( ship->shipclass == SHIP_PLATFORM )
   {
      send_to_char( "You can't do that here.\n" , ch );
      return;
   }

   if ( ship->lastdoc != ship->location )
   {
      send_to_char("&rYou don't seem to be docked right now.\n",ch);
      return;
   }

   if ( ship->shipstate != SHIP_LANDED && ship->shipstate != SHIP_DISABLED )
   {
      send_to_char("The ship is not docked right now.\n",ch);
      return;
   }
      
      if ( ship->shipstate == SHIP_DISABLED )
   {
      send_to_char("The ships drive is disabled .\n",ch);
      return;
   }
      
   chance = IS_NPC(ch) ? ch->top_level
   : (int)  (ch->pcdata->learned[gsn_hijack]) ;
   if ( number_percent( ) > chance )
   {  
      send_to_char("You fail to figure out the correct launch code.\n",ch);
      learn_from_failure( ch, gsn_hijack );
      return;
   }
                
   if ( ship->shipclass == FIGHTER_SHIP )
         chance = IS_NPC(ch) ? ch->top_level
      : (int)  (ch->pcdata->learned[gsn_starfighters]) ;
   if ( ship->shipclass == MIDSIZE_SHIP )
      chance = IS_NPC(ch) ? ch->top_level
   : (int)  (ch->pcdata->learned[gsn_midships]) ;
   if ( ship->shipclass == CAPITAL_SHIP )
      chance = IS_NPC(ch) ? ch->top_level
   : (int) (ch->pcdata->learned[gsn_capitalships]);
   if ( number_percent( ) < chance )
   {  
               
      if (ship->hatchopen)
      {
         ship->hatchopen = FALSE;
         echo_to_room( AT_YELLOW , get_room_index(ship->location) , str_printf("The hatch on %s closes." , ship->name) );
         echo_to_room( AT_YELLOW , get_room_index(ship->entrance) , "The hatch slides shut." );
      }
      set_char_color( AT_GREEN, ch );
      send_to_char( "Launch sequence initiated.\n", ch);
      act( AT_PLAIN, "$n starts up the ship and begins the launch sequence.", ch,
         NULL, argument , TO_ROOM );
      echo_to_ship( AT_YELLOW , ship , "The ship hums as it lifts off the ground.");
      echo_to_room( AT_YELLOW , get_room_index(ship->location) , str_printf("%s begins to launch.", ship->name) );
      ship->shipstate = SHIP_LAUNCH;
      ship->currspeed = ship->realspeed;
      if ( ship->shipclass == FIGHTER_SHIP )
         learn_from_success( ch, gsn_starfighters );
      if ( ship->shipclass == MIDSIZE_SHIP )
         learn_from_success( ch, gsn_midships );
      if ( ship->shipclass == CAPITAL_SHIP )
         learn_from_success( ch, gsn_capitalships );

      learn_from_success( ch, gsn_hijack );

      for (p = last_char; p ; p = p_prev )
      {
         p_prev = p->prev;  /* TRI */
         if (!IS_NPC(p) && get_trust(p) >= LEVEL_GOD)
         {
            buf2 = str_printf("%s(%s)", ship->name, ship->personalname );
            ch_printf(p, "&R[alarm] %s has been hijacked by %s!\n", buf2.c_str(), ch->name);
         }
      }

      if ( ship->alarm == 0 )
            return;
      if ( !str_cmp("Public",ship->owner) )
         return;
      for ( victim = first_char; victim; victim = victim->next )
      {
         bool victim_comlink;
         OBJ_DATA *obj;

         if ( !check_pilot(victim,ship) )
            continue;

         victim_comlink = FALSE;
         if ( IS_IMMORTAL(victim) )
            victim_comlink = TRUE;
         for ( obj = victim->last_carrying; obj; obj = obj->prev_content )
         {
            if ( obj->pIndexData->item_type == ITEM_COMLINK )
               victim_comlink = TRUE;
         }
         if ( !victim_comlink )
            continue;

         if ( !IS_NPC( victim ) && victim->switched )
            continue;

         if ( !IS_AWAKE(victim) || !victim->in_room || BV_IS_SET(victim->in_room->room_flags,ROOM_SILENCE) )
            continue;

         ch_printf(victim,"&R[alarm] %s has been hijacked!\n",ship->name);

      }

      return;
   }
   set_char_color( AT_RED, ch );
   send_to_char("You fail to work the controls properly!\n",ch);
   if ( ship->shipclass == FIGHTER_SHIP )
      learn_from_failure( ch, gsn_starfighters );
   if ( ship->shipclass == MIDSIZE_SHIP )
   learn_from_failure( ch, gsn_midships );
   if ( ship->shipclass == CAPITAL_SHIP )
      learn_from_failure( ch, gsn_capitalships );
   return; 
     
}

void do_add_patrol ( CHAR_DATA *ch , char *argument )
{
   std::string arg;
   int chance, credits;
   
   if ( IS_NPC( ch ) || !ch->pcdata )
   return;

   CHECK_SUBRESTRICTED( ch );

   arg = argument;    
   
   switch( ch->substate )
   { 
      default:
         if ( ch->backup_wait )
         {
            send_to_char( "&RYou already have backup coming.\n", ch );
            return;
         }
         
         if ( !ch->pcdata->clan )
         {
            send_to_char( "&RYou need to be a member of an organization before you can call for a guard.\n", ch );
            return;
         }    
         
         if ( ch->gold < ch->skill_level[LEADERSHIP_ABILITY] * 30 )
         {
            ch_printf( ch, "&RYou dont have enough credits.\n" );
            return;
         }    
         
         chance = (int) (ch->pcdata->learned[gsn_addpatrol]);
         if ( number_percent( ) < chance )
         {
            send_to_char( "&GYou begin making the call for reinforcements.\n", ch);
            act( AT_PLAIN, "$n begins issuing orders int $s comlink.", ch,
            NULL, arg , TO_ROOM );
            add_timer ( ch , TIMER_DO_FUN , 1 , do_add_patrol , 1 );
            ch->dest_buf = str_dup(arg);
            return;
         }
         send_to_char("&RYou call for a guard but nobody answers.\n",ch);
         learn_from_failure( ch, gsn_addpatrol );
         return;
     
      case 1:
         if ( !ch->dest_buf )
         {
            ch->substate = SUB_NONE;
            return;
         }
         arg = (const char*) ch->dest_buf;
         STR_DISPOSE( ch->dest_buf);
         break;
      
      case SUB_TIMER_DO_ABORT:
         if ( ch->dest_buf )
            STR_DISPOSE( ch->dest_buf );
         ch->substate = SUB_NONE;                                         
               send_to_char("&RYou are interupted before you can finish your call.\n", ch);
               return;
   }
    
   ch->substate = SUB_NONE;

   if ( !ch->pcdata->clan )
   {
      send_to_char( "&RYou are no longer a member of an organization.\n", ch );
      return;
   }

   credits = ch->skill_level[LEADERSHIP_ABILITY] * 30;
   if ( ch->gold < credits )
   {
      send_to_char( "&RYou dont have enough credits.\n", ch );
      return;
   }
   
   send_to_char( "&GYour guard is on the way.\n", ch);
   ch_printf( ch, "It cost you %d credits.\n", credits);
   ch->gold -= credits;

   learn_from_success( ch, gsn_addpatrol );
   
   if ( nifty_is_name( "empire" , ch->pcdata->clan->name ) )
      ch->backup_mob = MOB_VNUM_IMP_PATROL;
   else if ( nifty_is_name( "rebel" , ch->pcdata->clan->name ) )
      ch->backup_mob = MOB_VNUM_NR_PATROL;
   else
      ch->backup_mob = MOB_VNUM_MERC_PATROL;

   ch->backup_wait = 1;

}


void do_special_forces ( CHAR_DATA *ch , char *argument )
{
   std::string arg;
   int chance, credits;
   
   if ( IS_NPC( ch ) || !ch->pcdata )
   return;
   
   CHECK_SUBRESTRICTED( ch );

   arg = argument;    
   
   switch( ch->substate )
   { 
      default:
         if ( ch->backup_wait )
         {
            send_to_char( "&RYour reinforcements are already on the way.\n", ch );
            return;
         }

         if ( !ch->pcdata->clan )
         {
            send_to_char( "&RYou need to be a member of an organization before you can call for reinforcements.\n", ch );
            return;
         }

         if ( ch->gold < ch->skill_level[LEADERSHIP_ABILITY] * 350 )
         {
            ch_printf( ch, "&RYou dont have enough credits to send for reinforcements.\n" );
            return;
         }

         chance = (int) (ch->pcdata->learned[gsn_specialforces]);
         if ( number_percent( ) < chance )
         {
            send_to_char( "&GYou begin making the call for reinforcements.\n", ch);
            act( AT_PLAIN, "$n begins issuing orders int $s comlink.", ch,
            NULL, arg , TO_ROOM );
            add_timer ( ch , TIMER_DO_FUN , 1 , do_special_forces , 1 );
            ch->dest_buf = str_dup(arg);
            return;
         }
         send_to_char("&RYou call for reinforcements but nobody answers.\n",ch);
         learn_from_failure( ch, gsn_specialforces );
         return;

      case 1:
         if ( !ch->dest_buf )
         {
            ch->substate = SUB_NONE;
            return;
         }
         arg = (const char*) ch->dest_buf;
         STR_DISPOSE( ch->dest_buf);
         break;

      case SUB_TIMER_DO_ABORT:
         if ( ch->dest_buf )
            STR_DISPOSE( ch->dest_buf );
         ch->substate = SUB_NONE;
               send_to_char("&RYou are interupted before you can finish your call.\n", ch);
               return;
    }

    ch->substate = SUB_NONE;

    if ( !ch->pcdata->clan )
    {
       send_to_char( "&RYou are no longer a member of an organization.\n", ch );
       return;
    }

    credits = ch->skill_level[LEADERSHIP_ABILITY] * 350;
    if ( ch->gold < credits )
    {
       send_to_char( "&RYou dont have enough credits to send for reinforcements.\n", ch );
       return;
    }

    send_to_char( "&GYour reinforcements are on the way.\n", ch);
    ch_printf( ch, "It cost you %d credits.\n", credits);
    ch->gold -= credits;

    learn_from_success( ch, gsn_specialforces );

    if ( nifty_is_name( "empire" , ch->pcdata->clan->name ) )
       ch->backup_mob = MOB_VNUM_IMP_FORCES;
    else if ( nifty_is_name( "rebel" , ch->pcdata->clan->name ) )
       ch->backup_mob = MOB_VNUM_NR_FORCES;
    else
       ch->backup_mob = MOB_VNUM_MERC_FORCES;

    ch->backup_wait = number_range(1,2);

}


void do_elite_guard ( CHAR_DATA *ch , char *argument )
{
   std::string arg;
   int chance, credits;

   if ( IS_NPC( ch ) || !ch->pcdata )
   return;

   CHECK_SUBRESTRICTED( ch );

   arg = argument;

   switch( ch->substate )
   {
      default:
         if ( ch->backup_wait )
         {
            send_to_char( "&RYou already have backup coming.\n", ch );
            return;
         }

         if ( !ch->pcdata->clan )
         {
            send_to_char( "&RYou need to be a member of an organization before you can call for a guard.\n", ch );
            return;
         }

         if ( ch->gold < ch->skill_level[LEADERSHIP_ABILITY] * 200 )
         {
            ch_printf( ch, "&RYou dont have enough credits.\n" );
            return;
         }

         chance = (int) (ch->pcdata->learned[gsn_eliteguard]);
         if ( number_percent( ) < chance )
         {
            send_to_char( "&GYou begin making the call for reinforcements.\n", ch);
            act( AT_PLAIN, "$n begins issuing orders int $s comlink.", ch,
            NULL, arg , TO_ROOM );
            add_timer ( ch , TIMER_DO_FUN , 1 , do_elite_guard , 1 );
            ch->dest_buf = str_dup(arg);
            return;
         }
         send_to_char("&RYou call for a guard but nobody answers.\n",ch);
         learn_from_failure( ch, gsn_eliteguard );
         return;

      case 1:
         if ( !ch->dest_buf )
         {
            ch->substate = SUB_NONE;
            return;
         }
         arg = (const char*) ch->dest_buf;
         STR_DISPOSE( ch->dest_buf);
         break;

      case SUB_TIMER_DO_ABORT:
         if ( ch->dest_buf )
            STR_DISPOSE( ch->dest_buf );
         ch->substate = SUB_NONE;
               send_to_char("&RYou are interupted before you can finish your call.\n", ch);
               return;
    }

    ch->substate = SUB_NONE;

    if ( !ch->pcdata->clan )
    {
       send_to_char( "&RYou are no longer a member of an organization.\n", ch );
       return;
    }

    credits = ch->skill_level[LEADERSHIP_ABILITY] * 200;
    if ( ch->gold < credits )
    {
       send_to_char( "&RYou dont have enough credits.\n", ch );
       return;
    }

    send_to_char( "&GYour guard is on the way.\n", ch);

    ch_printf( ch, "It cost you %d credits.\n", credits);
    ch->gold -= credits;

    learn_from_success( ch, gsn_eliteguard );

    if ( nifty_is_name( "empire" , ch->pcdata->clan->name ) )

       ch->backup_mob = MOB_VNUM_IMP_ELITE;
    else if ( nifty_is_name( "rebel" , ch->pcdata->clan->name ) )
       ch->backup_mob = MOB_VNUM_NR_ELITE;
    else
       ch->backup_mob = MOB_VNUM_MERC_ELITE;

    ch->backup_wait = 1;

}

void do_unjail ( CHAR_DATA *ch , char *argument )
{
   CHAR_DATA *victim =NULL;
   std::string arg;

   one_argument(argument, arg);
   
   if ( ( victim = get_char_room( ch, arg ) ) == NULL )
   {
      send_to_char( "They aren't here.\n", ch );
      return;
   }
    
   if ( victim == ch )
   {
      send_to_char( "That's pointless.\n", ch );
      return;
   }

   if ( IS_NPC(victim) )
   {
      send_to_char( "That would be a waste of time.\n", ch );
      return;
   }

   if ( BV_IS_SET( ch->in_room->room_flags, ROOM_SAFE ) )
   {
      set_char_color( AT_MAGIC, ch );
      send_to_char( "This isn't a good place to do that.\n", ch );
      return;
   }

   if ( ch->position == POS_FIGHTING )
   {
      send_to_char( "Interesting combat technique.\n" , ch );
      return;
   }
   
   if ( ch->position <= POS_SLEEPING )
   {
      send_to_char( "In your dreams or what?\n" , ch );
      return;
   }

   if ( victim->in_room->vnum == 6)
   {
      send_to_char( "Only an immortal can unhell someone.\n", ch );
      return;
   }   
   send_to_char( "Jail restrictions released.\n", ch );
   victim->pcdata->jail_vnum = 0;
   victim->pcdata->release_date = 0;
   return;
}
void do_jail ( CHAR_DATA *ch , char *argument )
{
   CHAR_DATA *victim =NULL;
   CLAN_DATA   *clan =NULL;
   ROOM_INDEX_DATA *jail =NULL;
   std::string arg;
   std::string argstr = argument;
   sh_int time;
   bool h_d = FALSE;
   struct tm *tms;

   argstr = one_argument(argstr, arg);
   
   if ( IS_NPC (ch) || !ch->in_room ) return;
   
   if ( !ch->pcdata || ( clan = ch->pcdata->clan ) == NULL )
   {
      send_to_char( "Only members of organizations can jail their enemies.\n", ch );
      return;
   }

   jail = get_room_index( clan->jail );
   if ( !jail && clan->mainclan )
      jail = get_room_index( clan->mainclan->jail );
   
   if ( !jail )
   {
      send_to_char( "Your orginization does not have a suitable prison.\n", ch );
      return;
   }

   if ( jail->area && ch->in_room->area 
   && jail->area != ch->in_room->area &&
   ( !jail->area->planet || jail->area->planet != ch->in_room->area->planet ) )
   {
      send_to_char( "Your organizations prison is to far away.\n", ch );
      return;
   }
      
   if ( ch->mount )
   {
      send_to_char( "You can't do that while mounted.\n", ch );
      return;
   }

   if ( arg.empty() )
   {
      send_to_char( "Jail who?\n", ch );
      return;
   }

   if ( ( victim = get_char_room( ch, arg ) ) == NULL )
   {
      send_to_char( "They aren't here.\n", ch );
      return;
   }

   if ( victim == ch )
   {
      send_to_char( "That's pointless.\n", ch );
      return;
   }

   if ( IS_NPC(victim) )
   {
      send_to_char( "That would be a waste of time.\n", ch );
      return;
   }

   if ( BV_IS_SET( ch->in_room->room_flags, ROOM_SAFE ) )
   {
      set_char_color( AT_MAGIC, ch );
      send_to_char( "This isn't a good place to do that.\n", ch );
      return;
   }

   if ( ch->position == POS_FIGHTING )
   {
      send_to_char( "Interesting combat technique.\n" , ch );
      return;
   }
   
   if ( ch->position <= POS_SLEEPING )
   {
      send_to_char( "In your dreams or what?\n" , ch );
      return;
   }
   
   argstr = one_argument(argstr, arg);
   if ( arg.empty() || !is_number(arg) )
   {
      send_to_char( "Jail them for how long?\n", ch );
      return;
   }

   time = strtoi(arg);
   if ( time < 0 )
   {
      send_to_char( "You cannot hell for negative time.\n", ch );
      return;
   }
   time = strtoi(arg);
   if ( time == 0 && victim->in_room && victim->in_room->vnum != 6)
   {
      send_to_char( "Jail restrictions released.\n", ch );
      victim->pcdata->jail_vnum = 0;
      victim->pcdata->release_date = 0;
      return;
   }

   if ( victim->position >= POS_SLEEPING )
   {
      send_to_char( "You will have to stun them first.\n" , ch );
      return;
   }


   argstr = one_argument(argstr, arg);
   if ( arg.empty() || !str_prefix(arg, "hours") )
      h_d = TRUE;
   else if ( str_prefix(arg, "days") )
   {
      send_to_char( "Is that value in hours or days?\n", ch );
      return;
   }
   else if ( time > 30 )
   {
      send_to_char( "You may not hell a person for more than 30 days at a time.\n", ch );
      return;
   }
   tms = localtime(&current_time);
   if ( !tms )
      return;
   if ( h_d )
      tms->tm_hour += time;
   else
      tms->tm_mday += time;
   victim->pcdata->release_date = mktime(tms);
   if ( victim->pcdata->helled_by )
      STRFREE( victim->pcdata->helled_by );
   victim->pcdata->helled_by = STRALLOC(ch->name);
   victim->pcdata->jail_vnum = jail->vnum;
   ch_printf(ch, "%s will be released from jail at %24.24s.\n", victim->name,
            ctime(&victim->pcdata->release_date));
   act(AT_MAGIC, "$n is dragged away.", victim, NULL, ch, TO_NOTVICT);
   char_from_room(victim);
      char_to_room ( victim , jail );
   act(AT_MAGIC, "$n is dragged in.", victim, NULL, ch, TO_NOTVICT);
   do_look(victim, "auto");
   ch_printf(victim, "Whoops. You broke too many laws.\n"
            "You shall remain in jail for %d %s%s.\n", time,
            (h_d ? "hour" : "day"), (time == 1 ? "" : "s"));
   save_char_obj(victim); /* used to save ch, fixed by Thoric 09/17/96 */

   learn_from_success( ch , gsn_jail );

   return;
}

void do_smalltalk ( CHAR_DATA *ch , char *argument )
{
   std::string buf;
   std::string arg1;
   CHAR_DATA *victim = NULL;
   PLANET_DATA *planet = NULL;
   CLAN_DATA   *clan = NULL;
   
   if ( IS_NPC(ch) || !ch->pcdata )
   {
      send_to_char( "What would be the point of that.\n", ch );
      return;
   }

   if ( !ch->in_room )
      return;
   
   one_argument( argument, arg1 );

   if ( ch->mount )
   {
      send_to_char( "You can't do that while mounted.\n", ch );
      return;
   }

   if ( arg1.empty() )
   {
      send_to_char( "Create smalltalk with whom?\n", ch );
      return;
   }

   if ( ( victim = get_char_room( ch, arg1 ) ) == NULL )
   {
      send_to_char( "They aren't here.\n", ch );
      return;
   }

   if ( victim == ch )
   {
      send_to_char( "That's pointless.\n", ch );
      return;
   }

   if ( BV_IS_SET( ch->in_room->room_flags, ROOM_SAFE ) )
   {
      set_char_color( AT_MAGIC, ch );
      send_to_char( "This isn't a good place to do that.\n", ch );
      return;
   }

   if ( ch->position == POS_FIGHTING )
   {
      send_to_char( "Interesting combat technique.\n" , ch );
      return;
   }
   
   if ( victim->position == POS_FIGHTING )
   {
      send_to_char( "They're a little busy right now.\n" , ch );
      return;
   }
   

   if ( !IS_NPC(victim) || !victim->vip_flags.any() )
   {
      send_to_char( "Diplomacy would be wasted on them.\n" , ch );
      return;
   }
   
   if ( ch->position <= POS_SLEEPING )
   {
      send_to_char( "In your dreams or what?\n" , ch );
      return;
   }
   
   if ( victim->position <= POS_SLEEPING )
   {
      send_to_char( "You might want to wake them first...\n" , ch );
      return;
   }

   WAIT_STATE( ch, skill_table[gsn_smalltalk]->beats );

   if ( number_percent() - ch->skill_level[DIPLOMACY_ABILITY] + victim->top_level > ch->pcdata->learned[gsn_smalltalk] )
   {
      /*
      * Failure.
      */
      send_to_char( "You attempt to make smalltalk with them.. but are ignored.\n", ch );
      act( AT_ACTION, "$n is really getting on your nerves with all this chatter!\n", ch, NULL, victim, TO_VICT    );
      act( AT_ACTION, "$n asks $N about the weather but is ignored.\n",  ch, NULL, victim, TO_NOTVICT );

      if ( victim->alignment < -500 && victim->top_level >= ch->top_level+5 )
      {
         buf = str_printf("SHUT UP %s!", ch->name );
         do_yell(victim, buf.data());
               global_retcode = multi_hit( victim, ch, TYPE_UNDEFINED );
      }

      return;
   }
   
   send_to_char( "You strike up a short conversation with them.\n", ch );
   act( AT_ACTION, "$n smiles at you and says, 'hello'.\n", ch, NULL, victim, TO_VICT    );
   act( AT_ACTION, "$n chats briefly with $N.\n",  ch, NULL, victim, TO_NOTVICT );
   
   if ( IS_NPC(ch) || !ch->pcdata || !ch->pcdata->clan || !ch->in_room->area || !ch->in_room->area->planet )
      return;

   if ( ( clan = ch->pcdata->clan->mainclan ) == NULL )
      clan = ch->pcdata->clan;

   planet = ch->in_room->area->planet;

   if ( clan != planet->governed_by )
      return;

   planet->pop_support += 0.2;
   send_to_char( "Popular support for your organization increases slightly.\n", ch );

   gain_exp(ch, victim->top_level*10, DIPLOMACY_ABILITY);
   ch_printf( ch , "You gain %d diplomacy experience.\n", victim->top_level*10 );

   learn_from_success( ch, gsn_smalltalk );
      
   if ( planet->pop_support > 100 )
      planet->pop_support = 100;
}

void do_propeganda ( CHAR_DATA *ch , char *argument )
{
    std::string buf;
    std::string arg1;
    CHAR_DATA *victim;
    PLANET_DATA *planet;
    CLAN_DATA   *clan;

   if ( IS_NPC(ch) || !ch->pcdata || !ch->pcdata->clan
   ||   !ch->in_room || !ch->in_room->area || !ch->in_room->area->planet )
   {
       send_to_char( "What would be the point of that.\n", ch );
       return;
   }

    one_argument( argument, arg1 );

    if ( ch->mount )
    {
 send_to_char( "You can't do that while mounted.\n", ch );
 return;
    }

    if ( arg1.empty() )
    {
 send_to_char( "Spread propeganda to who?\n", ch );
 return;
    }

    if ( ( victim = get_char_room( ch, arg1 ) ) == NULL )
    {
 send_to_char( "They aren't here.\n", ch );
 return;
    }

    if ( victim == ch )
    {
 send_to_char( "That's pointless.\n", ch );
 return;
    }

    if ( BV_IS_SET( ch->in_room->room_flags, ROOM_SAFE ) )
    {
 set_char_color( AT_MAGIC, ch );
 send_to_char( "This isn't a good place to do that.\n", ch );
 return;
    }

    if ( ch->position == POS_FIGHTING )
    {
        send_to_char( "Interesting combat technique.\n" , ch );
        return;
    }

    if ( victim->position == POS_FIGHTING )
    {
        send_to_char( "They're a little busy right now.\n" , ch );
        return;
    }


    if ( !victim->vip_flags.any() )
    {
        send_to_char( "Diplomacy would be wasted on them.\n" , ch );
        return;
    }

    if ( ch->position <= POS_SLEEPING )
    {
        send_to_char( "In your dreams or what?\n" , ch );
        return;
    }

    if ( victim->position <= POS_SLEEPING )
    {
        send_to_char( "You might want to wake them first...\n" , ch );
        return;
    }

    planet = ch->in_room->area->planet;

    if ( ( clan = ch->pcdata->clan->mainclan ) == NULL )
       clan = ch->pcdata->clan;
    buf = str_printf(", and the evils of %s" , planet->governed_by ? planet->governed_by->name : "their current leaders" );
    ch_printf( ch, "You speak to them about the benefits of the %s%s.\n", ch->pcdata->clan->name,
        planet->governed_by == clan ? "" : buf.c_str() );
    act( AT_ACTION, "$n speaks about his organization.\n", ch, NULL, victim, TO_VICT    );
    act( AT_ACTION, "$n tells $N about their organization.\n",  ch, NULL, victim, TO_NOTVICT );
    WAIT_STATE( ch, skill_table[gsn_propeganda]->beats );

    if ( number_percent() + victim->top_level - get_curr_cha(ch) > ch->pcdata->learned[gsn_propeganda] )
    {
        send_to_char("Your words do little to sway them.\n", ch);
        if ( planet->governed_by != clan )
         {
            buf = str_printf("%s is a traitor!" , ch->name);
            do_yell(victim, buf.data());
                  global_retcode = multi_hit( victim, ch, TYPE_UNDEFINED );
         }

         return;
    }

    if ( planet->governed_by == clan )
    {
       planet->pop_support += .5 + ch->top_level/50;
       send_to_char( "Popular support for your organization increases.\n", ch );
    }
    else
    {
       planet->pop_support -= ch->top_level/50;
       send_to_char( "Popular support for the current government decreases.\n", ch );
    }

    gain_exp(ch, victim->top_level*100, DIPLOMACY_ABILITY);
    ch_printf( ch , "You gain %d diplomacy experience.\n", victim->top_level*100 );

    learn_from_success( ch, gsn_propeganda );

    if ( planet->pop_support > 100 )
        planet->pop_support = 100;
    if ( planet->pop_support < -100 )
        planet->pop_support = -100;

}

void do_bribe ( CHAR_DATA *ch , char *argument )
{
    std::string arg1;
    std::string argstr = argument;
    CHAR_DATA *victim;
    PLANET_DATA *planet;
    CLAN_DATA   *clan;
    int amount = 0;

    if ( IS_NPC(ch) || !ch->pcdata || !ch->pcdata->clan
    ||   !ch->in_room || !ch->in_room->area || !ch->in_room->area->planet )
    {
      send_to_char( "What would be the point of that.\n", ch );
      return;
    }
    
    argstr = one_argument( argstr, arg1 );

    if ( ch->mount )
    {
      send_to_char( "You can't do that while mounted.\n", ch );
      return;
    }

    if ( argstr[0] == '\0' )
    {
      send_to_char( "Bribe who how much?\n", ch );
      return;
    }

    amount = strtoi( argstr );

    if ( ( victim = get_char_room( ch, arg1 ) ) == NULL )
    {
      send_to_char( "They aren't here.\n", ch );
      return;
    }

    if ( victim == ch )
    {
      send_to_char( "That's pointless.\n", ch );
      return;
    }

    if ( BV_IS_SET( ch->in_room->room_flags, ROOM_SAFE ) )
    {
      set_char_color( AT_MAGIC, ch );
      send_to_char( "This isn't a good place to do that.\n", ch );
      return;
    }

    if ( amount <= 0 )
    {
      send_to_char( "A little bit more money would be a good plan.\n", ch );
      return;
    }
    
    if ( ch->gold < amount )
    {
        send_to_char( "Try getting that amount first.\n" , ch );
    return;
    }

    if ( ch->position == POS_FIGHTING )
    {
        send_to_char( "Interesting combat technique.\n" , ch );
        return;
    }
    
    if ( victim->position == POS_FIGHTING )
    {
        send_to_char( "They're a little busy right now.\n" , ch );
        return;
    }
    
    if ( ch->position <= POS_SLEEPING )
    {
        send_to_char( "In your dreams or what?\n" , ch );
        return;
    }
    
    if ( victim->position <= POS_SLEEPING )
    {
        send_to_char( "You might want to wake them first...\n" , ch );
        return;
    }

    if ( !victim->vip_flags.any() )
    {
        send_to_char( "Diplomacy would be wasted on them.\n" , ch );
        return;
    }
    
    ch->gold -= amount;
    victim->gold += amount;

    ch_printf( ch, "You give them a small gift on behalf of %s.\n", ch->pcdata->clan->name );
    act( AT_ACTION, "$n offers you a small bribe.\n", ch, NULL, victim, TO_VICT    );
    act( AT_ACTION, "$n gives $N some money.\n",  ch, NULL, victim, TO_NOTVICT );

    if ( !IS_NPC( victim ) )
       return;
    
    WAIT_STATE( ch, skill_table[gsn_bribe]->beats );

    if ( number_percent() - amount + victim->top_level > ch->pcdata->learned[gsn_bribe] )
        return;
            
    if ( ( clan = ch->pcdata->clan->mainclan ) == NULL )
       clan = ch->pcdata->clan;
       
    planet = ch->in_room->area->planet;
    
    
    if ( clan == planet->governed_by )
    {
      planet->pop_support += urange_float(0.1f, amount / 1000.0f, 2.0f); 
      send_to_char( "Popular support for your organization increases slightly.\n", ch );

      amount = UMIN( amount ,( exp_level(ch->skill_level[DIPLOMACY_ABILITY]+1) - exp_level(ch->skill_level[DIPLOMACY_ABILITY]) ) );

      gain_exp(ch, amount , DIPLOMACY_ABILITY);
      ch_printf( ch , "You gain %d diplomacy experience.\n", amount );

      learn_from_success( ch, gsn_bribe );
    }

    if ( planet->pop_support > 100 )
        planet->pop_support = 100;
}

void do_seduce ( CHAR_DATA *ch , char *argument )
{
  std::string arg;
  std::string buf;
  CHAR_DATA *victim;

  if ( IS_NPC(ch) || !ch->pcdata || !ch->in_room )
     return;

  one_argument( argument, arg );

  if ( arg.empty() )
  {
  send_to_char( "Seduce whom?\n", ch );
  return;
  }

  if ( ( victim = get_char_room( ch, arg ) ) == NULL )
  {
  send_to_char( "They aren't here.\n", ch );
  return;
  }


  if ( IS_AFFECTED(victim, AFF_CHARM) && victim->master )
  {
  act( AT_PLAIN, "But he'd rather follow $N!", ch, NULL, victim->master, TO_CHAR );
  return;
  }

      if ( circle_follow( victim, ch ) )
    {
 send_to_char( "Following in loops is not allowed... sorry.\n", ch );
 return;
    }


    WAIT_STATE( ch, skill_table[gsn_seduce]->beats );

    if ( number_percent() + victim->top_level - get_curr_cha(ch) > ch->pcdata->learned[gsn_seduce] )
    {
      send_to_char("You failed.\n", ch);
      buf = str_printf("%s failed to seduce you.\n", ch->name);
      send_to_char(buf, victim);
      global_retcode = multi_hit( victim, ch, TYPE_UNDEFINED );
      return;
 }


    if ( victim->master )
 stop_follower( victim );

   learn_from_success( ch, gsn_seduce );

    add_follower( victim, ch );
    return;

}

void do_mass_propeganda ( CHAR_DATA *ch , char *argument )
{
    std::string buf;
    CHAR_DATA *rch;
    PLANET_DATA *planet;
    CLAN_DATA   *clan;
    int victims = 0;

    if ( IS_NPC(ch) || !ch->pcdata || !ch->pcdata->clan || !ch->in_room->area || !ch->in_room->area->planet )
    {
        send_to_char( "What would be the point of that.\n", ch );
        return;
    }
        
    if ( ch->mount )
    {
        send_to_char( "You can't do that while mounted.\n", ch );
        return;
    }

    if ( BV_IS_SET( ch->in_room->room_flags, ROOM_SAFE ) )
    {
        set_char_color( AT_MAGIC, ch );
        send_to_char( "This isn't a good place to do that.\n", ch );
        return;
    }

    if ( ch->position == POS_FIGHTING )
    {
        send_to_char( "Interesting combat technique.\n" , ch );
        return;
    }
    
    if ( ch->position <= POS_SLEEPING )
    {
        send_to_char( "In your dreams or what?\n" , ch );
        return;
    }
    
    if ( ( clan = ch->pcdata->clan->mainclan ) == NULL )
       clan = ch->pcdata->clan;
       
    planet = ch->in_room->area->planet;
        
    buf = str_printf(", and the evils of %s" , planet->governed_by ? planet->governed_by->name : "their current leaders" );
    ch_printf( ch, "You speak to the people about the benifits of the %s%s.\n", ch->pcdata->clan->name,
        planet->governed_by == clan ? "" : buf.c_str() );
    act( AT_ACTION, "$n speaks about his organization.\n", ch, NULL, NULL, TO_ROOM    );

    WAIT_STATE( ch, skill_table[gsn_masspropeganda]->beats );

    if ( number_percent() < ch->pcdata->learned[gsn_masspropeganda]  ) 
    {
        for ( rch = ch->in_room->first_person; rch; rch = rch->next_in_room )
        {
            if ( rch == ch )
              continue;

            if (!IS_NPC(rch))
              continue;

            if ( !rch->vip_flags.any() )
              continue;

            if ( can_see( ch, rch ) )
              victims++;
            else
              continue;
        }
    
        if ( planet->governed_by == clan )
        { 
            planet->pop_support += (.5 + ch->top_level/10)*victims;
            send_to_char( "Popular support for your organization increases.\n", ch );
        }     
        else
        {
            planet->pop_support -= (ch->top_level/10)*victims;
            send_to_char( "Popular support for the current government decreases.\n", ch );
        }
    
        gain_exp(ch, ch->top_level*100, DIPLOMACY_ABILITY);
        ch_printf( ch , "You gain %d political experience.\n", ch->top_level*100 );
    
        learn_from_success( ch, gsn_masspropeganda );
        
        if ( planet->pop_support > 100 )
          planet->pop_support = 100;
        if ( planet->pop_support < -100 )
          planet->pop_support = -100;
        
        return;
    }
    else
    {
        send_to_char("They don't even seem interested in what you have to say.\n",ch);
        return;
    }
    return;
}

void do_gather_intelligence( CHAR_DATA *ch, char *argument )
{
    if ( !IS_NPC(ch) && !ch->pcdata )
        return;

    if ( argument[0] == '\0' )
    {
        send_to_char("You must input a name.\n", ch);
        return;
    }

    WAIT_STATE(ch, skill_table[gsn_gather_intelligence]->beats);
    CHAR_DATA *victim = get_char_world(ch, std::string("0.") + argument);
    if ( !victim )
    {
        send_to_char("You fail to gather information on that individual.\n", ch);
        return;
    }
    if ( IS_NPC(victim) )
    {
        send_to_char("This person has not made much of a name for themselves.\n", ch);
        return;
    }
    if ( ch == victim )
    {
        send_to_char("You already know enough about yourself.\n", ch);
        return;
    }
    if ( !IS_NPC(ch) && number_percent() > ch->pcdata->learned[gsn_gather_intelligence] )
    {
        learn_from_failure(ch, gsn_gather_intelligence);
        send_to_char("You fail to gather information on that individual.\n", ch);
        return;
    }

    learn_from_success(ch, gsn_gather_intelligence);
    const int report = number_percent();
    if ( report <= 44 )
    {
        const AREA_DATA *area = victim->in_room ? victim->in_room->area : nullptr;
        if ( area && area->planet )
            ch_printf(ch, "Information has been received that %s is on %s.\n", victim->name, area->planet->name);
        else
            ch_printf(ch, "Information has been received that %s is travelling.\n", victim->name);
    }
    else if ( report <= 54 )
    {
        const CLAN_DATA *clan = victim->pcdata ? victim->pcdata->clan : nullptr;
        if ( clan )
            ch_printf(ch, "%s seems to be involved with %s.\n", victim->name, clan->name);
        else
            ch_printf(ch, "%s does not seem to be involved with any organization.\n", victim->name);
    }
    else if ( report <= 72 )
    {
        if ( victim->hit < victim->max_hit / 4 )
            ch_printf(ch, "Hospital records show that %s has had a very serious injury and has not fully recovered.\n", victim->name);
        else if ( victim->hit < victim->max_hit / 2 )
            ch_printf(ch, "Hospital records show that %s has had a serious injury and has begun to recover.\n", victim->name);
        else if ( victim->hit < victim->max_hit )
            ch_printf(ch, "Hospital records show that %s has had a minor injury recently.\n", victim->name);
        else
            ch_printf(ch, "There is no recent medical history for %s.\n", victim->name);
    }
    else if ( report <= 91 )
    {
        const int ability = victim->main_ability;
        if ( ability >= 0 && ability < MAX_ABILITY )
            ch_printf(ch, "%s appears to have focused their training on %s.\n", victim->name, ability_name[ability]);
        else
            ch_printf(ch, "%s has no clear specialty.\n", victim->name);
    }
    else if ( victim->skill_level[FORCE_ABILITY] <= 1 )
        ch_printf(ch, "%s appears to have focused on mundane pursuits.\n", victim->name);
    else if ( victim->mana > 1000 )
        ch_printf(ch, "%s is rumored to excel at using the Force.\n", victim->name);
    else if ( victim->mana > 200 )
        ch_printf(ch, "%s is rumored to have some skill with the Force.\n", victim->name);
    else
        ch_printf(ch, "%s appears to have discovered a minor ability with the Force.\n", victim->name);
}


void do_bind ( CHAR_DATA *ch , char *argument )
{
  CHAR_DATA *victim;

    if ( IS_NPC(ch) || !ch->pcdata || !ch->in_room )
       return;
  
    if ( argument[0] == '\0' )
    {
 send_to_char( "Bind whom?\n", ch );
 return;
    }

    if ( ( victim = get_char_room( ch, argument ) ) == NULL )
    {
 send_to_char( "They aren't here.\n", ch );
 return;
    }

    if ( victim == ch )
    {
 send_to_char( "Suicide is a mortal sin.\n", ch );
 return;
    }

    if ( !IS_NPC(victim) && BV_IS_SET(victim->act, PLR_AFK) )
    {
      log_printf( "%s just bound %s with an afk flag on!." , ch->name, victim->name );
    }

    if ( is_safe( ch, victim ) )
       return;
        
    if ( IS_AFFECTED(ch, AFF_CHARM) ) 
    {
      if ( ch->master == victim )
      {
        act( AT_PLAIN, "$N is your beloved master.", ch, NULL, victim, TO_CHAR );
 return;
      }
    }

    if ( ch->position == POS_FIGHTING )
    {
 send_to_char( "You are a little busy!\n", ch );
 return;
    }

    ch->alignment -= 10;
    gmcp_evt_char_stats(ch);
    WAIT_STATE( ch, 1 * PULSE_VIOLENCE );

    if ( IS_NPC(victim) || number_percent( ) > ch->pcdata->learned[gsn_bind] )
    {
 send_to_char( "You fail.\n", ch );
 return;
    }

    if( victim->position != POS_STUNNED )    
      return;

    act( AT_YELLOW, "$n binds you up!",  ch, NULL, victim, TO_VICT    );
    act( AT_YELLOW, "You bind $N up.", ch, NULL, victim, TO_CHAR    );            
    act( AT_BLUE, "$n quickly binds $N, leaving $M helpless!", ch, NULL, victim, TO_NOTVICT );
    aff_paralysis( ch, victim );
    learn_from_success( ch, gsn_bind );
    return;
}    
  

bool aff_paralysis( CHAR_DATA *ch, CHAR_DATA *victim )
{
  AFFECT_DATA af;

  if ( !IS_AFFECTED( victim, AFF_PARALYSIS ) )
  {
  af.type      = gsn_stun;
  af.location  = APPLY_AC;
  af.modifier  = 20;
  af.duration  = 30;
  af.bitvector = AFF_PARALYSIS;
  affect_to_char( victim, &af );
  update_pos( victim );
  if ( IS_NPC(victim) )
   {
      start_hating( victim, ch );
      start_hunting( victim, ch );
      victim->was_stunned = 10;
   }
  }
  return TRUE;
}

void do_cutdoor( CHAR_DATA *ch, char *argument )
{
 EXIT_DATA *pexit;
 std::string arg;
  OBJ_DATA *wield;
 int whichweap; 
 int SABER = 1;
 int PIKE = 0;

   if ( ( wield = get_eq_char( ch, WEAR_WIELD ) ) == NULL ||
            ( ( !BV_IS_SET(wield->objflags,WEAPON_LIGHTSABER ) && ( !BV_IS_SET(wield->objflags,WEAPON_FORCE_PIKE) ) ) ) )
 {
     send_to_char( "You need a lightsaber for that!\n", ch );
     return;
 }

   if ( BV_IS_SET(wield->objflags,WEAPON_LIGHTSABER) )
      whichweap = SABER;
   else
      whichweap = PIKE;
 if ( !IS_NPC( ch )
 && ( (whichweap ? ch->pcdata->learned[gsn_lightsabers] <= 0 : ch->pcdata->learned[gsn_force_pikes] <= 0) || ch->pcdata->learned[gsn_cutdoor] <= 0 ))
 {
     send_to_char( "You can not use it well enough to cut a door open.\n", ch );
     return;
 }

 one_argument( argument, arg );

 if ( arg.empty() )
 {
     send_to_char( "Cut what?\n", ch );
     return;
 }

 if ( ch->fighting )
 {
     send_to_char( "You can't break off your fight.\n", ch );
     return;
 }

 pexit = find_door(ch, arg, FALSE);
 if ( pexit && !IS_SET(pexit->exit_info, EX_CLOSED) )
 {
     send_to_char("It is already open.\n", ch);
     return;
 }
 constexpr int move_cost = 15;
 if ( ch->move < move_cost )
 {
     send_to_char("You are too tired to cut through anything.\n", ch);
     return;
 }
 ch->move -= move_cost;
 gmcp_evt_char_vitals(ch);

 if ( pexit )
 {
     ROOM_INDEX_DATA *to_room;
     EXIT_DATA       *pexit_rev;
     int              chance;
     char     *keyword;

     WAIT_STATE( ch, skill_table[gsn_cutdoor]->beats );

     if ( IS_SET( pexit->exit_info, EX_SECRET ) )
  keyword = "wall";
     else
  keyword = pexit->keyword;
     if ( !IS_NPC(ch) )
  chance = ch->pcdata->learned[gsn_cutdoor] / 2;
     else
  chance = 90;

     if ( !IS_SET( pexit->exit_info, EX_BASHPROOF )
     &&   number_percent( ) < ( chance + 4 * ( get_curr_str( ch ) - 19 ) ) )
     {
  REMOVE_BIT( pexit->exit_info, EX_CLOSED );
  if ( IS_SET( pexit->exit_info, EX_LOCKED ) )
  REMOVE_BIT( pexit->exit_info, EX_LOCKED );
  SET_BIT( pexit->exit_info, EX_BASHED );

  act(AT_SKILL, "You cut open the $d!", ch, NULL, keyword, TO_CHAR );
  act(AT_SKILL, "$n cuts open the $d!",          ch, NULL, keyword, TO_ROOM );
  learn_from_success(ch, gsn_cutdoor);

  if ( (to_room = pexit->to_room) != NULL
  &&   (pexit_rev = pexit->rexit) != NULL
  &&    pexit_rev->to_room == ch->in_room )
  {
   CHAR_DATA *rch;

   REMOVE_BIT( pexit_rev->exit_info, EX_CLOSED );
   if ( IS_SET( pexit_rev->exit_info, EX_LOCKED ) )
     REMOVE_BIT( pexit_rev->exit_info, EX_LOCKED );
   SET_BIT( pexit_rev->exit_info, EX_BASHED );

   for ( rch = to_room->first_person; rch; rch = rch->next_in_room )
   {
       act(AT_SKILL, "The $d falls open!",
    rch, NULL, pexit_rev->keyword, TO_CHAR );
   }
  }
  damage( ch, ch, ( ch->max_hit / 20 ), gsn_cutdoor );

     }
     else
     {
  act(AT_SKILL, "You cut at the $d, but you handle it badly and just score it.",
   ch, NULL, keyword, TO_CHAR );
  act(AT_SKILL, "$n cuts at the $d, but just scores it.",
   ch, NULL, keyword, TO_ROOM );
  learn_from_failure(ch, gsn_cutdoor);
  damage( ch, ch, ( ch->max_hit / 20 ) + 10, gsn_cutdoor );
     }
 }    
 else
 {
     act(AT_SKILL, "You slice at the wall, but just score it.",
  ch, NULL, NULL, TO_CHAR );
     act(AT_SKILL, "$n cuts at the wall, but just scores it.",
  ch, NULL, NULL, TO_ROOM );
     learn_from_failure(ch, gsn_cutdoor);
     damage( ch, ch, ( ch->max_hit / 20 ) + 10, gsn_cutdoor );
 }
 if ( char_died(ch) || !ch->in_room )
     return;

 ROOM_INDEX_DATA *room = ch->in_room;
 for ( CHAR_DATA *gch = room->first_person, *gch_next; gch; gch = gch_next )
 {
     gch_next = gch->next_in_room;
     if ( gch != ch && !char_died(gch) && gch->in_room == room
     &&   IS_AWAKE(gch) && !gch->fighting
     &&   IS_NPC(gch) && !IS_AFFECTED(gch, AFF_CHARM)
     &&   ch->top_level - gch->top_level <= 4 && number_bits(2) == 0 )
     {
         multi_hit(gch, ch, TYPE_UNDEFINED);
         if ( char_died(ch) || ch->in_room != room )
             return;
     }
 }
}

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
*		     Character saving and loading module		   *
****************************************************************************/

#define _POSIX_C_SOURCE 200809L

#include <sys/types.h>
#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/stat.h>
#include <dirent.h>
#include <signal.h>
#include <fcntl.h>
#include <cerrno>
#include <climits>
#include <cstdint>
#include <fstream>
#include <limits>
#include <sstream>
#include "mud.h"

/*
 * Increment with every major format change.
 */
#define SAVEVERSION	3


CHAR_DATA *quitting_char, *loading_char, *saving_char;


/*
 * Externals
 */
void fwrite_comments( CHAR_DATA *ch, FILE *fp );
void fread_comment( CHAR_DATA *ch, FILE *fp );

extern int top_ed;

/*
 * Local functions.
 */

static void fwrite_char( CHAR_DATA *ch, FILE *fp );
static void fread_char( CHAR_DATA *ch, FILE *fp, bool preload, int &file_version, long *password_offset );
static void fread_obj_versioned(GameContext *game, CHAR_DATA *ch, FILE *fp, sh_int os_type,
                               int file_version, ObjectNest &nest, EquipmentSlots *equipment = nullptr);
void	write_corpses	args( ( CHAR_DATA *ch, const std::string& name ) );
CHAR_DATA *  fread_mobile( GameContext *game, FILE *fp, int file_version = SAVEVERSION );
void write_char_mobile( CHAR_DATA *ch , char *argument );
void read_char_mobile( GameContext *game, char *argument );
void fwrite_mobile( FILE *fp, CHAR_DATA *mob );


void save_home( CHAR_DATA *ch )
{
    if ( ch->plr_home )
    {
       	FILE *fp;
       	std::string filename;
       	sh_int templvl;
       	OBJ_DATA *contents;


       	filename = str_printf("%s%c/%s.home", PLAYER_DIR, tolower(ch->name[0]),
				 capitalize( ch->name ).c_str() );
    	if ( ( fp = fopen( filename.c_str(), "w" ) ) == NULL )
    	{
    	}
    	else
    	{
		templvl = ch->top_level;
		ch->top_level = LEVEL_HERO;		/* make sure EQ doesn't get lost */
        	contents = ch->plr_home->last_content;
        	if (contents)
	  	  fwrite_obj(ch, contents, fp, 0, OS_CARRY );
		fprintf( fp, "#END\n" );
		ch->top_level = templvl;
    		FCLOSE( fp );
    	}
    }
}

/*
 * Un-equip character before saving to ensure proper	-Thoric
 * stats are saved in case of changes to or removal of EQ
 */
void de_equip_char(CHAR_DATA *ch, EquipmentSlots &equipment)
{
    equipment = {};

    for (OBJ_DATA *obj = ch->first_carrying; obj; obj = obj->next_content)
	if ( is_valid_wear_location( obj->wear_loc ) )
	{
	    auto &layers = equipment[obj->wear_loc];
	    const auto empty_slot = std::find(layers.begin(), layers.end(), nullptr);
	    if (empty_slot == layers.end())
		bug("%s had on more than %d layers of clothing in one location (%d): %s",
		    ch->name, MAX_LAYERS, obj->wear_loc, obj->name);
	    else
		*empty_slot = obj;
	    unequip_char(ch, obj);
	}
}

/*
 * Re-equip character					-Thoric
 */
void re_equip_char(CHAR_DATA *ch, const EquipmentSlots &equipment)
{
    if (quitting_char == ch)
        return;

    for (int wear = 0; wear < MAX_WEAR; ++wear)
        for (OBJ_DATA *obj : equipment[wear])
            if (obj)
                equip_char(ch, obj, wear);
}


static bool create_save_bucket( const char *root, char initial )
{
    const std::string bucket = std::string(root) + initial;
    for ( const char *path : {root, bucket.c_str()} )
    {
        if ( mkdir( path, 0750 ) == 0 )
            continue;
        if ( errno == EEXIST )
        {
            struct stat info;
            if ( stat( path, &info ) == 0 )
            {
                if ( S_ISDIR( info.st_mode ) )
                    continue;
                errno = ENOTDIR;
            }
        }
        bug( "Save_char_obj: cannot create directory %s: %s", path, strerror(errno) );
        return false;
    }
    return true;
}

static FILE *open_pfile_temp( const std::string &path, std::string &temp_path )
{
    temp_path = path + ".tmp.XXXXXX";
    const int fd = mkstemp( temp_path.data() );
    if ( fd < 0 )
        return nullptr;

    FILE *fp = fdopen( fd, "wb" );
    if ( !fp )
    {
        const int open_error = errno;
        close( fd );
        unlink( temp_path.c_str() );
        errno = open_error;
    }
    return fp;
}

static bool close_pfile( FILE *fp )
{
    const bool complete = !ferror(fp) && fflush(fp) == 0 && fsync(fileno(fp)) == 0;
    const bool closed = fclose(fp) == 0;
    return complete && closed;
}

static bool sync_save_directory( const std::string &path )
{
    const int fd = open( path.c_str(), O_RDONLY | O_DIRECTORY );
    if ( fd < 0 )
        return false;
    const bool synced = fsync( fd ) == 0;
    const bool closed = close( fd ) == 0;
    return synced && closed;
}

bool read_saved_bounty_transaction( const std::string &name, std::string &transaction )
{
    if ( name.empty() || name.find_first_not_of( "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz" ) != std::string::npos )
        return false;

    const char initial = tolower( static_cast<unsigned char>(name[0]) );
    const std::string bucket = std::string( PLAYER_DIR ) + initial;
    std::ifstream input( bucket + "/" + capitalize( name ) );
    std::string field;
    if ( !(input >> field) || field != "#PLAYER" || !(input >> field) )
        return false;
    if ( field == "Version" )
    {
        int version;
        if ( !(input >> version >> field) )
            return false;
    }

    std::string token;
    if ( field == "BountyTxn" )
    {
        if ( !(input >> token >> field) || token.size() != 64
             || token.find_first_not_of( "0123456789abcdef" ) != std::string::npos )
            return false;
    }
    if ( field != "Toplevel" && field != "Name" )
        return false;

    if ( !sync_save_directory( bucket ) || !sync_save_directory( PLAYER_DIR ) )
        return false;

    transaction = token;
    return true;
}

static bool copy_pfile_data( FILE *input, FILE *output, long remaining = -1 )
{
    char buffer[8192];
    while ( remaining != 0 )
    {
        const size_t capacity = remaining < 0 ? sizeof(buffer)
            : std::min( sizeof(buffer), static_cast<size_t>( remaining ) );
        const size_t length = fread( buffer, 1, capacity, input );
        if ( fwrite( buffer, 1, length, output ) != length )
            return false;
        if ( length < capacity )
            return remaining < 0 && !ferror( input );
        if ( remaining > 0 )
            remaining -= length;
    }
    return !ferror( input );
}

bool replace_player_password( const CHAR_DATA *ch, long offset, const char *digest )
{
    if ( offset < 0 )
        return false;

    const std::string path = str_printf( "%s%c/%s", PLAYER_DIR,
        tolower( static_cast<unsigned char>( ch->name[0] ) ), capitalize( ch->name ).c_str() );
    FILE *input = fopen( path.c_str(), "rb" );
    if ( !input )
        return false;

    const std::string previous = std::string( ch->pcdata->pwd ) + "~";
    std::string field( previous.size(), '\0' );
    std::string temporary;
    FILE *output = open_pfile_temp( path, temporary );
    if ( !output )
    {
        fclose( input );
        return false;
    }

    const bool copied = copy_pfile_data( input, output, offset )
        && fread( field.data(), 1, field.size(), input ) == field.size() && field == previous
        && fprintf( output, "%s~", digest ) > 0 && copy_pfile_data( input, output );
    const bool input_closed = fclose( input ) == 0;
    const bool output_closed = close_pfile( output );
    if ( copied && input_closed && output_closed && rename( temporary.c_str(), path.c_str() ) == 0 )
        return true;

    bug( "Cannot update password field for %s", ch->name );
    unlink( temporary.c_str() );
    return false;
}

static void backup_pfile( const std::string &path, const std::string &backup_path )
{
    FILE *input = fopen( path.c_str(), "rb" );
    if ( !input )
    {
        if ( errno != ENOENT )
            bug( "Save_char_obj: cannot read previous save %s: %s", path.c_str(), strerror(errno) );
        return;
    }

    std::string temp_path;
    FILE *output = open_pfile_temp( backup_path, temp_path );
    if ( !output )
    {
        bug( "Save_char_obj: cannot open backup for %s: %s", path.c_str(), strerror(errno) );
        fclose( input );
        return;
    }

    const bool copied = copy_pfile_data( input, output );
    const bool input_closed = fclose(input) == 0;
    const bool output_closed = close_pfile(output);
    if ( copied && input_closed && output_closed
         && rename( temp_path.c_str(), backup_path.c_str() ) == 0 )
        return;

    bug( "Save_char_obj: backup failed for %s", path.c_str() );
    unlink( temp_path.c_str() );
}

/*
 * Save a character and inventory.
 */
bool save_char_obj( CHAR_DATA *ch )
{
    FILE *fp;

    if ( !ch )
    {
	bug( "Save_char_obj: null ch!" );
	return false;
    }

    if ( IS_NPC(ch) || !ch->pcdata || NOT_AUTHED(ch) )
	return false;

    if ( ch->desc && ch->desc->original )
	ch = ch->desc->original;

    if ( !ch->name || !ch->name[0] || !ch->pcdata )
	return false;

    saving_char = ch;
    /* Keep the player's clan data in sync. */
    if ( ch->pcdata->clan )
	save_clan( ch->pcdata->clan );

    const char initial = tolower( static_cast<unsigned char>(ch->name[0]) );
    const std::string name = capitalize( ch->name );
    const std::string strsave = str_printf( "%s%c/%s", PLAYER_DIR, initial, name.c_str() );

    /*
     * Save immortal stats, level & vnums for wizlist		-Thoric
     * and do_vnums command
     *
     * Also save the player flags so we the wizlist builder can see
     * who is a guest and who is retired.
     */
    if ( get_trust(ch) > LEVEL_HERO )
    {
      const std::string god_file = str_printf( "%s%s", GOD_DIR, name.c_str() );

      if ( ( fp = fopen( god_file.c_str(), "w" ) ) == NULL )
      {
	bug( "Save_god_level: fopen" );
	perror( god_file.c_str() );
      }
      else
      {
	fprintf( fp, "Level        %d\n", ch->top_level );
	fwrite_bitset(fp, "PcflagsEx", ch->pcdata->flags);
//	fprintf( fp, "Pcflags      %d\n", bitset_to_int(ch->pcdata->flags) );
	if ( ch->pcdata->r_range_lo && ch->pcdata->r_range_hi )
	  fprintf( fp, "RoomRange    %d %d\n", ch->pcdata->r_range_lo,
	  				       ch->pcdata->r_range_hi	);
	if ( ch->pcdata->o_range_lo && ch->pcdata->o_range_hi )
	  fprintf( fp, "ObjRange     %d %d\n", ch->pcdata->o_range_lo,
	  				       ch->pcdata->o_range_hi	);
	if ( ch->pcdata->m_range_lo && ch->pcdata->m_range_hi )
	  fprintf( fp, "MobRange     %d %d\n", ch->pcdata->m_range_lo,
	  				       ch->pcdata->m_range_hi	);
	FCLOSE( fp );
      }
    }

    bool saved = false;
    if ( create_save_bucket( PLAYER_DIR, initial ) )
    {
        std::string temp_path;
        fp = open_pfile_temp( strsave, temp_path );
        if ( !fp )
            bug( "Save_char_obj: cannot open new save for %s: %s", strsave.c_str(), strerror(errno) );
        else
        {
            EquipmentSlots equipment;
            de_equip_char(ch, equipment);
            fwrite_char( ch, fp );
            if ( ch->first_carrying )
                fwrite_obj(ch, ch->last_carrying, fp, 0, OS_CARRY, &equipment);
            if ( ch->pcdata->comments )
                fwrite_comments( ch, fp );
            if ( ch->pcdata->pet )
                fwrite_mobile( fp, ch->pcdata->pet );
            fprintf( fp, "#END\n" );
            const bool complete = close_pfile( fp );
            re_equip_char(ch, equipment);

            if ( complete )
            {
                if ( BV_IS_SET( ch->game->get_sysdata()->save_flags, SV_BACKUP )
                     && create_save_bucket( BACKUP_DIR, initial ) )
                {
                    const std::string backup_path = str_printf( "%s%c/%s", BACKUP_DIR, initial, name.c_str() );
                    backup_pfile( strsave, backup_path );
                }
                saved = rename( temp_path.c_str(), strsave.c_str() ) == 0;
                if ( !saved )
                    bug( "Save_char_obj: cannot publish save %s: %s", strsave.c_str(), strerror(errno) );
            }
            else
                bug( "Save_char_obj: cannot finish writing %s", strsave.c_str() );

            if ( saved )
                ch->pcdata->save_time = current_time;
            else
                unlink( temp_path.c_str() );
        }
    }

    write_corpses(ch, "");
    quitting_char = NULL;
    saving_char   = NULL;
    return saved;
}

void save_clone( CHAR_DATA *ch )
{
    std::string strsave, strback;
    FILE *fp;

    if ( !ch )
    {
	bug( "Save_char_obj: null ch!" );
	return;
    }

    if ( IS_NPC(ch) || NOT_AUTHED(ch) )
	return;

    if ( ch->desc && ch->desc->original )
	ch = ch->desc->original;

    saving_char = ch;
    EquipmentSlots equipment;
    de_equip_char(ch, equipment);
    ch->pcdata->clones++;
    
    ch->pcdata->save_time = current_time;
    strsave = str_printf("%s%c/%s.clone", PLAYER_DIR, tolower(static_cast<unsigned char>(ch->name[0])),
				 capitalize( ch->name ).c_str() );

    /*
     * Auto-backup pfile (can cause lag with high disk access situtations
     */
    if ( BV_IS_SET( ch->game->get_sysdata()->save_flags, SV_BACKUP ) )
    {
	strback = str_printf("%s%c/%s.clone", BACKUP_DIR, tolower(static_cast<unsigned char>(ch->name[0])),
				 capitalize( ch->name ).c_str() );
	rename( strsave.c_str(), strback.c_str() );
    }

    if ( ( fp = fopen( strsave.c_str(), "w" ) ) == NULL )
    {
	bug( "Save_char_obj: fopen" );
	perror( strsave.c_str() );
    }
    else
    {
	fwrite_char( ch, fp );
	if ( ch->pcdata && ch->pcdata->comments )         /* comments */
	  fwrite_comments( ch, fp );        /* comments */
	fprintf( fp, "#END\n" );
	FCLOSE( fp );
    }

    ch->pcdata->clones--;
    re_equip_char(ch, equipment);

    write_corpses(ch, "");
    quitting_char = NULL;
    saving_char   = NULL;
    return;
}

void fwrite_bitset_noCR(FILE *fp, const char *name, const FLAG_SET &bv)
{
	if (name != NULL)
    	fprintf(fp, "%s ", name);

    for (size_t word = 0; word < bv.bits.size(); word++)
    {
        uint64_t val = bv.bits[word];

        while (val)
        {
            int bit = __builtin_ctzll(val);
            fprintf(fp, "%zu ", word * 64 + bit);
            val &= val - 1;
        }
    }
    fprintf(fp, "-1");	
}

void fwrite_bitset(FILE *fp, const char *name, const FLAG_SET &bv)
{
	fwrite_bitset_noCR(fp, name, bv);
    fprintf(fp, "\n");
}

void fread_bitset(FILE *fp, FLAG_SET &bv)
{
    bv.reset();
    if ( !fp )
        return;

    for (;;)
    {
        int c;
        do
        {
            c = getc( fp );
        }
        while ( c != EOF && isspace( static_cast<unsigned char>(c) ) );

        if ( c == EOF || ferror( fp ) )
        {
            bug( "fread_bitset: EOF encountered before -1 terminator" );
            break;
        }

        int bit;
        if ( c == '-' || c == '+' )
        {
            int next = getc( fp );
            if ( next == EOF || ferror( fp ) )
            {
                bug( "fread_bitset: EOF encountered after sign before -1 terminator" );
                break;
            }
            if ( !isdigit( static_cast<unsigned char>(next) ) )
            {
                bug( "fread_bitset: unexpected char '%c' after '%c' before -1 terminator", next, c );
                ungetc( next, fp );
                fread_to_eol( fp );
                break;
            }
            ungetc( next, fp );
            bit = fread_number( fp );
            if ( c == '-' )
                bit = -bit;
        }
        else if ( !isdigit( static_cast<unsigned char>(c) ) )
        {
            bug( "fread_bitset: unexpected char '%c' before -1 terminator", c );
            ungetc( c, fp );
            fread_to_eol( fp );
            break;
        }
        else
        {
            ungetc( c, fp );
            bit = fread_number( fp );
        }

        if ( bit < 0 )
            break;

        if ( bit > 20000 )   // pick a sane upper bound
        {
            bug( "fread_bitset: insane bit %d", bit );
            fread_to_eol( fp );   // discard rest of line
            break;
        }

        bv.set((size_t)bit);

        if ( feof( fp ) || ferror( fp ) )
            break;
    }
}

void call_to_stop()
{
	;
}

void fread_bitset_compat(FILE *fp, FLAG_SET &bv)
{
    bv.reset();

    constexpr size_t max_bitset_line = MAX_BITS * 6 + 2;
    std::string line;
    int c;
    while ((c = getc(fp)) != EOF && c != '\n' && c != '\r')
    {
        if (line.size() <= max_bitset_line)
            line.push_back(static_cast<char>(c));
    }
    if (line.size() > max_bitset_line)
    {
        bug("fread_bitset_compat: line too long");
        return;
    }
    const char *ln = line.c_str();
    std::vector<int> vals;

    const char *ptr = ln;
    while (*ptr)
    {
        while (isspace(static_cast<unsigned char>(*ptr)))
            ++ptr;
        if (!*ptr)
            break;

        errno = 0;
        char *end = nullptr;
        long v = strtol(ptr, &end, 10);
        if (end == ptr)
        {
            bug("fread_bitset_compat: non-numeric token in line '%s'", ln);
            break;
        }
        ptr = end;
        if (errno == ERANGE || v < INT_MIN || v > INT_MAX)
        {
            bug("fread_bitset_compat: integer out of range in line '%s'", ln);
            continue;
        }
        vals.push_back(static_cast<int>(v));
    }

    if (vals.empty())
        return;

    /*
     * Check for NEW format (ends with -1)
     */
    if (vals.back() == -1)
    {
        for (size_t i = 0; i + 1 < vals.size(); ++i)
        {
            if (vals[i] >= MAX_BITS)
            {
                bug("fread_bitset_compat: insane bit %d", vals[i]);
                continue;
            }
            if (vals[i] >= 0)
                bv.set(static_cast<size_t>(vals[i]));
        }
        return;
    }

    /*
     * Check for OLD formats (8 values):
     *   1) Legacy 32-bit bitmask in vals[0] followed by seven 0s
     *   2) 8-element boolean array (all 0/1)
     */
    if (vals.size() == 8)
    {
        bool trailing_zeros = true;
        for (size_t i = 1; i < 8; ++i)
        {
            if (vals[i] != 0)
            {
                trailing_zeros = false;
                break;
            }
        }
        if (trailing_zeros)
        {
            if (vals[0] != 0)
                bv = int_to_bitset(vals[0]);
            return;
        }

        bool all_bool = true;
        for (size_t i = 0; i < 8; ++i)
        {
            if (vals[i] != 0 && vals[i] != 1)
            {
                all_bool = false;
                break;
            }
        }
        if (all_bool)
        {
            for (size_t i = 0; i < 8; ++i)
            {
                if (vals[i])
                    bv.set(i);
            }
            return;
        }
    }

    /*
     * Fallback: treat as new format without -1 (tolerant mode)
     */
    for (int val : vals)
    {
        if (val >= MAX_BITS)
        {
            bug("fread_bitset_compat: insane bit %d", val);
            continue;
        }
        if (val >= 0)
            bv.set(static_cast<size_t>(val));
    }
}


/*
 * Write the char.
 */
static void fwrite_char( CHAR_DATA *ch, FILE *fp )
{
    AFFECT_DATA *paf;
    ALIAS_DATA *pal;
    int sn, track, drug;
    SKILLTYPE *skill;

    fprintf( fp, "#PLAYER\n" );

    fprintf( fp, "Version      %d\n",   SAVEVERSION		);
    if ( !ch->pcdata->bounty_transaction.empty() )
        fprintf( fp, "BountyTxn    %s\n", ch->pcdata->bounty_transaction.c_str() );
    fprintf( fp, "Toplevel     %d\n",   ch->top_level           );
    fprintf( fp, "Name         %s~\n",	ch->name		);
    if ( ch->short_descr && ch->short_descr[0] != '\0' )
      fprintf( fp, "ShortDescr   %s~\n",	ch->short_descr	);
    if ( ch->long_descr && ch->long_descr[0] != '\0' )
      fprintf( fp, "LongDescr    %s~\n",	ch->long_descr	);
    if ( ch->description && ch->description[0] != '\0' )
      fprintf( fp, "Description  %s~\n",	ch->description	);
    fprintf( fp, "Sex          %d\n",	ch->sex			);
    fprintf( fp, "Race         %d\n",	ch->race		);
    fprintf( fp, "MainAbility  %d\n",	ch->main_ability	);
    fprintf( fp, "Speaking    %d\n", ch->speaking );
	fwrite_bitset( fp, "Speaks", ch->speaks);
    if ( ch->trust )
      fprintf( fp, "Trust        %d\n",	ch->trust		);
    {
      long long played_total = std::max( 0LL, ch->pcdata->played );
      if ( ch->pcdata->logon > 0 && current_time > ch->pcdata->logon )
      {
        const auto elapsed = static_cast<long long>( current_time - ch->pcdata->logon );
        played_total += std::min( elapsed, LLONG_MAX - played_total );
      }
      fprintf( fp, "Played       %lld\n", played_total );
    }
    fprintf( fp, "Room         %d\n",
	(  ch->in_room == get_room_index( ROOM_VNUM_LIMBO )
	&& ch->was_in_room )
	    ? ch->was_in_room->vnum
	    : ( ch->in_room ? ch->in_room->vnum : ROOM_VNUM_LIMBO ) );
    if ( ch->plr_home != NULL )
    {
       fprintf( fp, "PlrHome      %d\n", ch->plr_home->vnum );
       if ( ch->pcdata->home_generation > 0 )
           fprintf( fp, "HomeGeneration %d\n", ch->pcdata->home_generation );
    }

    fprintf( fp, "HpManaMove   %d %d 0 0 %d %d\n",
	ch->hit, ch->max_hit, ch->move, ch->max_move );
    fprintf( fp, "Force        %d %d %d %d\n", ch->perm_frc, ch->mod_frc, ch->mana, ch->max_mana );
    fprintf( fp, "Gold         %d\n",	ch->gold		);
    fprintf( fp, "Bank         %ld\n",	ch->pcdata->bank		);
    { 
       int ability;
       for ( ability = 0 ; ability < MAX_ABILITY ; ability++ )
           fprintf( fp, "Ability        %d %d %ld\n",	
              ability , ch->skill_level[ability] , ch->experience[ability] );
    }
    fprintf( fp, "Salary_time         %ld\n",	ch->pcdata->salary_date );
    fprintf( fp, "Salary         %d\n",	ch->pcdata->salary		);
    fprintf( fp, "Clones         %d\n",	ch->pcdata->clones		);
    fprintf( fp, "Questpoints         %d\n",	ch->questpoints		);
    fprintf( fp, "Nextquest         %d\n",	ch->nextquest		);
    fprintf( fp, "Jailvnum         %d\n",	ch->pcdata->jail_vnum	);
    fwrite_bitset( fp, "ActEx", ch->act );
    if ( ch->affected_by.any() )
	  fwrite_bitset(fp, "AffectedByEx",ch->affected_by);
    fprintf( fp, "Position     %d\n",
        ch->position == POS_FIGHTING ? POS_STANDING : ch->position );

    fprintf( fp, "SavingThrows %d %d %d %d %d\n",
    		  ch->saving_poison_death,
		  ch->saving_wand,
    		  ch->saving_para_petri,
    		  ch->saving_breath,
    		  ch->saving_spell_staff			);
    fprintf( fp, "Alignment    %d\n",	ch->alignment		);
    fprintf( fp, "Glory        %d\n",   ch->pcdata->quest_curr  );
    fprintf( fp, "MGlory       %d\n",   ch->pcdata->quest_accum );
    fprintf( fp, "Hitroll      %d\n",	ch->hitroll		);
    fprintf( fp, "Damroll      %d\n",	ch->damroll		);
    fprintf( fp, "Armor        %d\n",	ch->armor		);
    if ( ch->wimpy )
      fprintf( fp, "Wimpy        %d\n",	ch->wimpy		);
    fwrite_bitset( fp, "Channels", ch->channels );
    if ( ch->resistant.any() )
	  fwrite_bitset(fp, "ResistantEx", ch->resistant);
    if ( ch->immune.any() )
	  fwrite_bitset(fp, "ImmuneEx", ch->immune);
    if ( ch->susceptible.any() )
	  fwrite_bitset(fp, "SusceptibleEx", ch->susceptible);
    if ( ch->pcdata && ch->pcdata->outcast_time )
      fprintf( fp, "Outcast_time %ld\n",ch->pcdata->outcast_time );
    if ( ch->pcdata && ch->pcdata->restore_time )
      fprintf( fp, "Restore_time %ld\n",ch->pcdata->restore_time );
    if ( ch->mental_state != -10 )
      fprintf( fp, "Mentalstate  %d\n",	ch->mental_state	);

    {
	fprintf( fp, "Password     %s~\n",	ch->pcdata->pwd		);
	fprintf( fp, "Lastplayed   %lld\n", static_cast<long long>( current_time ) );
	if ( ch->pcdata->bamfin && ch->pcdata->bamfin[0] != '\0' )
	  fprintf( fp, "Bamfin       %s~\n",	ch->pcdata->bamfin	);
	if ( ch->pcdata->email && ch->pcdata->email[0] != '\0' )
	  fprintf( fp, "Email       %s~\n",	ch->pcdata->email	);
	if ( ch->pcdata->bamfout && ch->pcdata->bamfout[0] != '\0' )
	  fprintf( fp, "Bamfout      %s~\n",	ch->pcdata->bamfout	);
	if ( ch->pcdata->rank && ch->pcdata->rank[0] != '\0' )
	  fprintf( fp, "Rank         %s~\n",	ch->pcdata->rank	);
	if ( ch->pcdata->bestowments && ch->pcdata->bestowments[0] != '\0' )
	  fprintf( fp, "Bestowments  %s~\n", 	ch->pcdata->bestowments );
	fprintf( fp, "Title        %s~\n",	ch->pcdata->title	);
	if ( ch->pcdata->homepage && ch->pcdata->homepage[0] != '\0' )
	  fprintf( fp, "Homepage     %s~\n",	ch->pcdata->homepage	);
	if ( ch->pcdata->bio && ch->pcdata->bio[0] != '\0' )
	  fprintf( fp, "Bio          %s~\n",	ch->pcdata->bio 	);
	if ( ch->pcdata->authed_by && ch->pcdata->authed_by[0] != '\0' )
	  fprintf( fp, "AuthedBy     %s~\n",	ch->pcdata->authed_by	);
	if ( ch->pcdata->min_snoop )
	  fprintf( fp, "Minsnoop     %d\n",	ch->pcdata->min_snoop	);
	if ( ch->pcdata->prompt && *ch->pcdata->prompt )
	  fprintf( fp, "Prompt       %s~\n",	ch->pcdata->prompt	);
	if ( ch->pcdata->pagerlen != 24 )
	  fprintf( fp, "Pagerlen     %d\n",	ch->pcdata->pagerlen	);
//  fprintf(fp, "Commandgroup    %d\n", ch->pcdata->commandgroup);
    fwrite_bitset( fp, "CommandGroupEx",  ch->pcdata->commandgroup  );		
        for ( pal = ch->pcdata->first_alias; pal; pal = pal->next )
        {
            if ( !pal->name || !pal->cmd || !*pal->name || !*pal->cmd )
                continue;
            fprintf( fp, "Alias        %s~ %s~\n",
                     pal->name,
                     pal->cmd
                   );
        }
	fprintf( fp, "Addiction   ");
	for ( drug = 0 ; drug <=9 ; drug++ )
	  fprintf( fp, " %d",	ch->pcdata->addiction[drug] );
	fprintf( fp, "\n");
	fprintf( fp, "Druglevel   ");
	for ( drug = 0 ; drug <=9 ; drug++ )
	  fprintf( fp, " %d",	ch->pcdata->drug_level[drug] );
	fprintf( fp, "\n");
	if ( ch->pcdata->wanted_flags.any() )
	  fwrite_bitset(fp, "WantedEx", ch->pcdata->wanted_flags);
//	  fprintf( fp, "Wanted       %d\n",	ch->pcdata->wanted_flags );
	
	if ( IS_IMMORTAL( ch ) || ch->pcdata->area )
	{
	  fprintf( fp, "WizInvis     %d\n", ch->pcdata->wizinvis );
	  if ( ch->pcdata->r_range_lo && ch->pcdata->r_range_hi )
	    fprintf( fp, "RoomRange    %d %d\n", ch->pcdata->r_range_lo,
	  					 ch->pcdata->r_range_hi	);
	  if ( ch->pcdata->o_range_lo && ch->pcdata->o_range_hi )
	    fprintf( fp, "ObjRange     %d %d\n", ch->pcdata->o_range_lo,
	  					 ch->pcdata->o_range_hi	);
	  if ( ch->pcdata->m_range_lo && ch->pcdata->m_range_hi )
	    fprintf( fp, "MobRange     %d %d\n", ch->pcdata->m_range_lo,
	  					 ch->pcdata->m_range_hi	);
	}
	if ( ch->pcdata->clan_name && ch->pcdata->clan_name[0] != '\0' )
	  fprintf( fp, "Clan         %s~\n",	ch->pcdata->clan_name	);
	fwrite_bitset(fp, "FlagsEx", ch->pcdata->flags);
        if ( ch->pcdata->release_date > current_time )
            fprintf( fp, "Helled       %lld %s~\n",
                static_cast<long long>( ch->pcdata->release_date ), ch->pcdata->helled_by );
	if ( ch->pcdata->pkills )
	  fprintf( fp, "PKills       %d\n",	ch->pcdata->pkills	);
	if ( ch->pcdata->pdeaths )
	  fprintf( fp, "PDeaths      %d\n",	ch->pcdata->pdeaths	);
        if ( ch->pcdata->target && ch->pcdata->target[0] != '\0' )
      	  fprintf( fp, "Targ %s~\n",	ch->pcdata->target	);
 	if ( get_timer( ch , TIMER_PKILLED)       
         && ( get_timer( ch , TIMER_PKILLED) > 0 ) )
         fprintf( fp, "PTimer       %d\n",     get_timer(ch, TIMER_PKILLED));
        fprintf( fp, "MKills       %d\n",	ch->pcdata->mkills	);
	fprintf( fp, "MDeaths      %d\n",	ch->pcdata->mdeaths	);
	if ( ch->pcdata->illegal_pk )
	  fprintf( fp, "IllegalPK    %d\n",	ch->pcdata->illegal_pk	);
	fprintf( fp, "AttrPerm     %d %d %d %d %d %d %d\n",
	    ch->perm_str,
	    ch->perm_int,
	    ch->perm_wis,
	    ch->perm_dex,
	    ch->perm_con,
	    ch->perm_cha,
	    ch->perm_lck );

	fprintf( fp, "AttrMod      %d %d %d %d %d %d %d\n",
	    ch->mod_str, 
	    ch->mod_int, 
	    ch->mod_wis,
	    ch->mod_dex, 
	    ch->mod_con,
	    ch->mod_cha, 
	    ch->mod_lck );

	fprintf( fp, "Condition    %d %d %d %d\n",
	    ch->pcdata->condition[0],
	    ch->pcdata->condition[1],
	    ch->pcdata->condition[2],
	    ch->pcdata->condition[3] );
	if ( ch->desc && ch->desc->host )
            fprintf( fp, "Site         %s\n", ch->desc->host );
	else
            fprintf( fp, "Site         (Link-Dead)\n" );
	
	for ( sn = 1; sn < top_sn; sn++ )
	{
	    if ( skill_table[sn]->name && ch->pcdata->learned[sn] > 0 )
		switch( skill_table[sn]->type )
		{
		    default:
			fprintf( fp, "Skill        %d '%s'\n",
			  ch->pcdata->learned[sn], skill_table[sn]->name );
			break;
		    case SKILL_SPELL:
			fprintf( fp, "Spell        %d '%s'\n",
			  ch->pcdata->learned[sn], skill_table[sn]->name );
			break;
		    case SKILL_WEAPON:
			fprintf( fp, "Weapon       %d '%s'\n",
			  ch->pcdata->learned[sn], skill_table[sn]->name );
			break;
		    case SKILL_TONGUE:
			fprintf( fp, "Tongue       %d '%s'\n",
			  ch->pcdata->learned[sn], skill_table[sn]->name );
			break;
		}
	}
    }

    for ( paf = ch->first_affect; paf; paf = paf->next )
    {
	if ( paf->type >= 0 && (skill=get_skilltype(paf->type)) == NULL )
	    continue;

	if ( paf->type >= 0 && paf->type < TYPE_PERSONAL )
	  fprintf( fp, "AffectDataEx   '%s' %3d %3d %3d %10d\n",
	    skill->name,
	    paf->duration,
	    paf->modifier,
	    paf->location,
	    paf->bitvector
	    );
	else
	  fprintf( fp, "AffectEx       %3d %3d %3d %3d %10d\n",
	    paf->type,
	    paf->duration,
	    paf->modifier,
	    paf->location,
	    paf->bitvector
	    );
    }

    track = URANGE( 2, ((ch->top_level+3) * MAX_KILLTRACK)/LEVEL_AVATAR, MAX_KILLTRACK );
    for ( sn = 0; sn < track; sn++ )
    {
	if ( ch->pcdata->killed[sn].vnum == 0 )
	  break;
	fprintf( fp, "Killed       %d %d\n",
		ch->pcdata->killed[sn].vnum,
		ch->pcdata->killed[sn].count );
    }

    fprintf( fp, "End\n\n" );
    return;
}



/*
 * Write an object and its contents.
 */
void fwrite_obj( CHAR_DATA *ch, OBJ_DATA *obj, FILE *fp, int iNest,
		 sh_int os_type, const EquipmentSlots *equipment )
{
    EXTRA_DESCR_DATA *ed;
    AFFECT_DATA *paf;
    int wear_loc = WEAR_NONE;

    if ( iNest >= MAX_NEST )
    {
	bug( "fwrite_obj: iNest hit MAX_NEST %d", iNest );
	return;
    }

	if( !obj )
	{
		bug( "%s: NULL obj", __FUNCTION__ );
		return;
	}

    /*
     * Slick recursion to write lists backwards,
     *   so loading them will load in forwards order.
     */
    if ( obj->prev_content && os_type != OS_CORPSE )
	fwrite_obj(ch, obj->prev_content, fp, iNest, OS_CARRY, equipment);

    /*
     * Castrate storage characters.
     */
    if ( obj->item_type == ITEM_KEY && !IS_OBJ_STAT(obj, ITEM_CLANOBJECT ))
	return;

    /*
     * Catch deleted objects					-Thoric
     */
    if ( obj_extracted(obj) )
	return;

    /*
     * Do NOT save prototype items!				-Thoric
     */
    if ( IS_OBJ_STAT( obj, ITEM_PROTOTYPE ) )
	return;

    /* Corpse saving. -- Altrag */
    fprintf( fp, (os_type == OS_CORPSE ? "#CORPSE\n" : "#OBJECT\n") );

    if ( iNest )
		fprintf( fp, "Nest         %d\n",	iNest		     );
    if ( obj->count > 1 )
		fprintf( fp, "Count        %d\n",	obj->count	     );
	if( obj->name && ( !obj->pIndexData->name  || str_cmp( obj->name, obj->pIndexData->name ) ) )
		fprintf( fp, "Name         %s~\n", obj->name );
	if( obj->short_descr && ( !obj->pIndexData->short_descr || str_cmp( obj->short_descr, obj->pIndexData->short_descr ) ) )
		fprintf( fp, "ShortDescr   %s~\n", obj->short_descr );
	if( obj->description && ( !obj->pIndexData->description || str_cmp( obj->description, obj->pIndexData->description ) ) )
		fprintf( fp, "Description  %s~\n", obj->description );
	if( obj->action_desc && ( !obj->pIndexData->action_desc || str_cmp( obj->action_desc, obj->pIndexData->action_desc ) ) )
		fprintf( fp, "ActionDesc   %s~\n", obj->action_desc );
    fprintf( fp, "Vnum         %d\n",	obj->pIndexData->vnum	     );
    if ( os_type == OS_CORPSE && obj->in_room )
    	fprintf( fp, "Room         %d\n",   obj->in_room->vnum         );
    if ( obj->wear_flags != obj->pIndexData->wear_flags )
		fwrite_bitset(fp,"WearFlagsEx", obj->wear_flags);
//		fprintf( fp, "WearFlags    %d\n",	obj->wear_flags	     );
    if (equipment)
        for (int wear = 0; wear < MAX_WEAR; ++wear)
        {
            const auto &layers = (*equipment)[wear];
            if (std::find(layers.begin(), layers.end(), obj) != layers.end())
            {
                wear_loc = wear;
                break;
            }
        }
    if ( wear_loc != WEAR_NONE )
	fprintf( fp, "WearLoc      %d\n",	wear_loc	     );
    if ( obj->item_type != obj->pIndexData->item_type )
	fprintf( fp, "ItemType     %d\n",	obj->item_type	     );
    if ( obj->weight != obj->pIndexData->weight )
      fprintf( fp, "Weight       %d\n",	obj->weight		     );
    if ( obj->level )
      fprintf( fp, "Level        %d\n",	obj->level		     );
    if ( obj->timer )
      fprintf( fp, "Timer        %d\n",	obj->timer		     );
    if ( obj->cost != obj->pIndexData->cost )
      fprintf( fp, "Cost         %d\n",	obj->cost		     );
    if ( obj->value[0] || obj->value[1] || obj->value[2]
    ||   obj->value[3] || obj->value[4] || obj->value[5] )
      fprintf( fp, "Values       %d %d %d %d %d %d\n",
	obj->value[0], obj->value[1], obj->value[2],
	obj->value[3], obj->value[4], obj->value[5]     );
	fwrite_bitset(fp, "Objflags", obj->objflags);
    switch ( obj->item_type )
    {
    case ITEM_PILL: /* was down there with staff and wand, wrongly - Scryn */
    case ITEM_POTION:
	if ( IS_VALID_SN(obj->value[1]) )
	    fprintf( fp, "Spell 1      '%s'\n",
		skill_table[obj->value[1]]->name );

	if ( IS_VALID_SN(obj->value[2]) )
	    fprintf( fp, "Spell 2      '%s'\n",
		skill_table[obj->value[2]]->name );

	if ( IS_VALID_SN(obj->value[3]) )
	    fprintf( fp, "Spell 3      '%s'\n",
		skill_table[obj->value[3]]->name );

	break;

    case ITEM_DEVICE:
	if ( IS_VALID_SN(obj->value[3]) )
	    fprintf( fp, "Spell 3      '%s'\n",
		skill_table[obj->value[3]]->name );

	break;
    case ITEM_SALVE:
	if ( IS_VALID_SN(obj->value[4]) )
	    fprintf( fp, "Spell 4      '%s'\n",
		skill_table[obj->value[4]]->name );

	if ( IS_VALID_SN(obj->value[5]) )
	    fprintf( fp, "Spell 5      '%s'\n",
		skill_table[obj->value[5]]->name );
	break;
    }
	if (obj->trig_flags.any() )
		fwrite_bitset(fp, "Triggerflags", obj->trig_flags);

    for ( paf = obj->first_affect; paf; paf = paf->next )
    {
	/*
	 * Save extra object affects				-Thoric
	 */
	if ( paf->type < 0 || paf->type >= top_sn )
	{
	  fprintf( fp, "AffectEx       %d %d %d %d %d\n",
	    paf->type,
	    paf->duration,
	     ((paf->location == APPLY_WEAPONSPELL
	    || paf->location == APPLY_WEARSPELL
	    || paf->location == APPLY_REMOVESPELL
	    || paf->location == APPLY_STRIPSN)
	    && IS_VALID_SN(paf->modifier))
	    ? skill_table[paf->modifier]->slot : paf->modifier,
	    paf->location,
	    paf->bitvector
	    );
	}
	else
	  fprintf( fp, "AffectDataEx   '%s' %d %d %d %d\n",
	    skill_table[paf->type]->name,
	    paf->duration,
	     ((paf->location == APPLY_WEAPONSPELL
	    || paf->location == APPLY_WEARSPELL
	    || paf->location == APPLY_REMOVESPELL
	    || paf->location == APPLY_STRIPSN)
	    && IS_VALID_SN(paf->modifier))
	    ? skill_table[paf->modifier]->slot : paf->modifier,
	    paf->location,
	    paf->bitvector
	    );
    }

    for ( ed = obj->first_extradesc; ed; ed = ed->next )
	fprintf( fp, "ExtraDescr   %s~ %s~\n",
	    ed->keyword, ed->description );

    fprintf( fp, "End\n\n" );

    if ( obj->first_content )
	fwrite_obj(ch, obj->last_content, fp, iNest + 1, OS_CARRY, equipment);

    return;
}



/*
 * Load a char and inventory into a new ch structure.
 */
bool load_char_obj( DESCRIPTOR_DATA *d, const std::string& name, bool preload, long *password_offset )
{
  return load_char_obj_v2(d,name,preload,0,password_offset);
}

/*
 * Load a char and inventory into a new ch structure with option of restoring from backup.
 */
bool load_char_obj_v2( DESCRIPTOR_DATA *d, const std::string& name, bool preload, int undead, long *password_offset )
{
    std::string strsave;
    CHAR_DATA *ch;
    FILE *fp;
    bool found;
    bool from_backup = undead != 0;
    if ( password_offset )
        *password_offset = -1;
    int file_version = 0;
    struct stat fst;
    EquipmentSlots equipment{};
    ObjectNest nest{};
    extern FILE *fpArea;
    extern char strArea[MAX_INPUT_LENGTH];
//  char buf[MAX_INPUT_LENGTH];
    
    CREATE( ch, CHAR_DATA, 1 );
	ch->game = d->game;
    clear_char( ch );
    loading_char = ch;

	ch->game = d->game;
	ch->pcdata = new PC_DATA();
	ch->pcdata->logon = current_time;
    d->character			= ch;
    ch->on				= NULL;
    ch->desc				= d;
    ch->name				= STRALLOC( name );
    ch->act.reset();
	BV_SET_BIT(ch->act,PLR_BLANK);	
	BV_SET_BIT(ch->act,PLR_COMBINE);
	BV_SET_BIT(ch->act,PLR_PROMPT);	
    ch->channels.set_range( 0, CHANNEL_MAX - 1 );
    ch->perm_str			= 10;
    ch->perm_int			= 10;
    ch->perm_wis			= 10;
    ch->perm_dex			= 10;
    ch->perm_con			= 10;
    ch->perm_cha			= 10;
    ch->perm_lck			= 10;
    ch->pcdata->condition[COND_THIRST]	= 48;
    ch->pcdata->condition[COND_FULL]	= 48;
    ch->pcdata->condition[COND_BLOODTHIRST] = 10;
    ch->pcdata->wizinvis		= 0;
    ch->mental_state			= -10;
    ch->mobinvis			= 0;
    for (int i = 0; i < MAX_SKILL; ++i)
        ch->pcdata->learned[i]		= 0;
    ch->pcdata->release_date		= 0;
    ch->pcdata->helled_by		= NULL;
    ch->pcdata->pet			= NULL;
    ch->saving_poison_death 		= 0;
    ch->saving_wand			= 0;
    ch->saving_para_petri		= 0;
    ch->saving_breath			= 0;
    ch->saving_spell_staff		= 0;
    ch->pcdata->comments                = NULL;    /* comments */
    ch->pcdata->pagerlen		= 24;
    ch->mob_clan                        = STRALLOC( "" );
    ch->was_sentinel                    = NULL;
    ch->plr_home                        = NULL;
    found = FALSE;
    if (undead) {
      strsave = str_printf("%s%c/%s", BACKUP_DIR, tolower(name[0]),
                        capitalize( name ).c_str() );
    } else {
      strsave = str_printf("%s%c/%s", PLAYER_DIR, tolower(name[0]),
			capitalize( name ).c_str() );
    }
    if ( stat( strsave.c_str(), &fst ) != -1 )
    {
      if ( fst.st_size == 0 )
      {
	from_backup = true;
	strsave = str_printf("%s%c/%s", BACKUP_DIR, tolower(name[0]),
			capitalize( name ).c_str() );
	send_to_char( "Restoring your backup player file...", ch );
      }
      else
      {
	log_printf_plus( LOG_COMM, LEVEL_GREATER, "%s player data for: %s (%dK)",
	  preload ? "Preloading" : "Loading", ch->name,
	  (int) fst.st_size/1024 );
      }
    }
    /* else no player file */

    if ( ( fp = fopen( strsave.c_str(), "r" ) ) != NULL )
    {
        long field_offset = -1;
        bool read_player = false;

	found = TRUE;
	/* Cheat so that bug will show line #'s -- Altrag */
	fpArea = fp;
	SPRINTF(strArea, "%s", strsave.c_str());
	for ( ; ; )
	{
	    char letter;
	    char *word;

	    letter = fread_letter( fp );
	    if ( letter == '*' )
	    {
		fread_to_eol( fp );
		continue;
	    }

	    if ( letter != '#' )
	    {
		bug( "Load_char_obj: # not found." );
		bug( "%s", name.c_str() );
		break;
	    }

	    word = fread_word( fp );
	    if ( !str_cmp( word, "PLAYER" ) )
	    {
		fread_char( ch, fp, preload, file_version,
                    password_offset && !read_player ? &field_offset : nullptr );
                if ( read_player )
                    field_offset = -1;
                read_player = true;
		if ( preload )
		  break;
	    }
	    else
	    if ( !str_cmp( word, "OBJECT" ) )	/* Objects	*/
		fread_obj_versioned(d->game, ch, fp, OS_CARRY, file_version, nest, &equipment);
	    else
	    if ( !str_cmp( word, "COMMENT") )
		fread_comment(ch, fp );		/* Comments	*/
	    else
	    if ( !strcmp( word, "MOBILE") )
	    {
		CHAR_DATA *mob;
		if (!d->game)
		{
		  bug( "Load_char_obj: Mobile not loaded as no game was found on the char." );
		  bug( "%s", name.c_str() );
		  break;
		}
		mob = fread_mobile( d->game, fp, file_version );
		if ( mob )
		{
		    ch->pcdata->pet = mob;
		    mob->master = ch;
		    BV_SET_BIT(mob->affected_by, AFF_CHARM);
		}
	    }
	    else
	    if ( !str_cmp( word, "END"    ) )	/* Done		*/
            {
                if ( password_offset && !from_backup && !preload && !ferror( fp ) )
                    *password_offset = field_offset;
		break;
            }
	    else
	    {
		bug( "Load_char_obj: bad section." );
		bug( "%s", name.c_str() );
		break;
	    }
	}
	FCLOSE( fp );
	fpArea = NULL;
	SPRINTF(strArea, "$");
    }

    
    if ( !found )
    {
			
	ch->short_descr			= STRALLOC( "" );
	ch->long_descr			= STRALLOC( "" );
	ch->description			= STRALLOC( "" );
    	ch->pcdata->target		= STRALLOC( "" );
	ch->editor			= NULL;
        ch->nextquest 				= 0;
        ch->questpoints				= 0;
        ch->pcdata->clones			= 0;
        ch->pcdata->jail_vnum 			= 0;
	ch->pcdata->clan_name		= STRALLOC( "" );
	ch->pcdata->clan		= NULL;
	ch->pcdata->pwd			= str_dup( "" );
	ch->pcdata->email		= str_dup( "" );
	ch->pcdata->bamfin		= str_dup( "" );
	ch->pcdata->bamfout		= str_dup( "" );
	ch->pcdata->rank		= str_dup( "" );
	ch->pcdata->bestowments		= str_dup( "" );
	ch->pcdata->title		= STRALLOC( "" );
	ch->pcdata->homepage		= str_dup( "" );
	ch->pcdata->bio 		= STRALLOC( "" );
	ch->pcdata->authed_by		= STRALLOC( "" );
	ch->pcdata->prompt		= STRALLOC( "" );
	ch->pcdata->r_range_lo		= 0;
	ch->pcdata->r_range_hi		= 0;
	ch->pcdata->m_range_lo		= 0;
	ch->pcdata->m_range_hi		= 0;
	ch->pcdata->o_range_lo		= 0;
	ch->pcdata->o_range_hi		= 0;
	ch->pcdata->wizinvis		= 0;
        ch->pcdata->first_alias		= NULL;
        ch->pcdata->last_alias		= NULL;
        ch->on 				= NULL;
    }
    else
    {
    	ch->on				= NULL;
	if ( !ch->plr_home || !BV_IS_SET(ch->plr_home->room_flags, ROOM_PLR_HOME)
	     || ch->pcdata->home_generation < 0
	     || ch->pcdata->home_generation != ch->plr_home->home_generation )
	{
	    ch->plr_home = nullptr;
	    ch->pcdata->home_generation = 0;
	    BV_REMOVE_BIT( ch->act, PLR_HOME_RESIDENT );
	}
	if ( ch->in_room && ch->in_room != ch->plr_home
	     && BV_IS_SET( ch->in_room->room_flags, ROOM_PLR_HOME ) )
	    ch->in_room = nullptr;
	if ( !ch->pcdata->clan_name )
	{
	  ch->pcdata->clan_name	= STRALLOC( "" );
	  ch->pcdata->clan	= NULL;
	}
        if ( !ch->pcdata->bio )
          ch->pcdata->bio	 = STRALLOC( "" );

	if ( !ch->pcdata->authed_by )
	  ch->pcdata->authed_by	 = STRALLOC( "" );

	if ( !IS_NPC( ch ) && get_trust( ch ) > LEVEL_AVATAR )
	{
	  if ( ch->pcdata->wizinvis < 2 )
	    ch->pcdata->wizinvis = ch->top_level;
 	  assign_area( ch );
	}
	if (file_version > 1)
	    re_equip_char(ch, equipment);

    }
    loading_char = NULL;
    return found;
}



/*
 * Read in a char.
 */

#if defined(KEY)
#undef KEY
#endif

#define KEY( literal, field, value )					\
				if ( !str_cmp( word, literal ) )	\
				{					\
				    field  = value;			\
				    fMatch = TRUE;			\
				    break;				\
				}

template <typename Integer>
static Integer fread_pfile_integer( FILE *fp )
{
    const char *word = fread_word( fp );
    char *end;
    errno = 0;
    const long long value = strtoll( word, &end, 10 );
    if ( end == word || *end != '\0' )
    {
        bug( "fread_char: invalid integer field" );
        return 0;
    }
    if ( errno == ERANGE || value < std::numeric_limits<Integer>::min()
                         || value > std::numeric_limits<Integer>::max() )
    {
        bug( "fread_char: integer field out of range" );
        return value < 0 ? std::numeric_limits<Integer>::min() : std::numeric_limits<Integer>::max();
    }
    return static_cast<Integer>( value );
}

static void fread_char( CHAR_DATA *ch, FILE *fp, bool preload, int &file_version, long *password_offset )
{
    std::string buf;
    char *line;
    const char *word;
    int x1, x2, x3, x4, x5, x6, x7, x8, x9, x0;
    sh_int killcnt;
    bool fMatch;
    bool read_password = false;
    time_t lastplayed = 0;
    int sn;//, extra;
         
    file_version = 0;
    killcnt = 0;

    for ( ; ; )
    {
		word = ( feof( fp ) ? "End" : fread_word( fp ) );

		if( word[0] == '\0' )
		{
			bug( "%s: EOF encountered reading file!", __FUNCTION__ );
			word = "End";
		}
        if ( preload && ( !str_cmp( word, "Site" ) || !str_cmp( word, "Skill" )
                         || !str_cmp( word, "Spell" ) || !str_cmp( word, "Tongue" )
                         || !str_cmp( word, "Weapon" ) ) )
            word = "End";
		fMatch = FALSE;

	switch ( UPPER(word[0]) )
	{
	case '*':
	    fMatch = TRUE;
	    fread_to_eol( fp );
	    break;

	case 'A':
		if ( !str_cmp( word, "Act"  ) )
		{
			ch->act = int_to_bitset(fread_number( fp ));
			fMatch = TRUE;
			break;
		}
		if ( !str_cmp( word, "ActEx"  ) )
		{
			fread_bitset(fp, ch->act);
			fMatch = TRUE;
			break;
		}
//	    KEY( "Act",		ch->act,		fread_number( fp ) );
		if ( !str_cmp( word, "AffectedBy"  ) )
		{
			int x = -1;
			x = (fread_number( fp ));
			if (x == 0)
			  ch->affected_by.reset();
			else
				ch->affected_by = int_to_bitset(x);
			fMatch = TRUE;
			break;
		}
		if ( !str_cmp( word, "AffectedByEx"  ) )
		{
			fread_bitset(fp, ch->affected_by);
			fMatch = TRUE;
			break;
		}
//	    KEY( "AffectedBy",	ch->affected_by,	fread_number( fp ) );
	    KEY( "Alignment",	ch->alignment,		fread_number( fp ) );
	    KEY( "Armor",	ch->armor,		fread_number( fp ) );
            
            if ( !str_cmp( word, "Addiction"  ) )
	    {
		line = fread_line( fp );
		x0=x1=x2=x3=x4=x5=x6=x7=x8=x9=0;
		sscanf( line, "%d %d %d %d %d %d %d %d %d %d",
		      &x0, &x1, &x2, &x3, &x4, &x5, &x6, &x7, &x8, &x9 );
		ch->pcdata->addiction[0] = x0;
		ch->pcdata->addiction[1] = x1;
		ch->pcdata->addiction[2] = x2;
		ch->pcdata->addiction[3] = x3;
		ch->pcdata->addiction[4] = x4;
		ch->pcdata->addiction[5] = x5;
		ch->pcdata->addiction[6] = x6;
		ch->pcdata->addiction[7] = x7;
		ch->pcdata->addiction[8] = x8;
		ch->pcdata->addiction[9] = x9;
		fMatch = TRUE;
		break;
	    }
            
            if ( !str_cmp( word, "Ability"  ) )
	    {
		std::istringstream values( fread_line( fp ) );
		long experience;
		if ( ( values >> x0 >> x1 >> experience )
		     && x0 >= 0 && x0 < MAX_ABILITY )
		{
		      ch->skill_level[x0] = x1;
		      ch->experience[x0] = experience;
		}
		fMatch = TRUE;
		break;
	    }
 
             
	    if ( !str_cmp( word, "Affect" ) || !str_cmp( word, "AffectData" ) )
	    {
			AFFECT_DATA *paf;

			if ( preload )
			{
				fMatch = TRUE;
				fread_to_eol( fp );
				break;
			}
			CREATE( paf, AFFECT_DATA, 1 );
			if ( !str_cmp( word, "Affect" ) )
			{
				paf->type	= fread_number( fp );
			}
			else
			{
				char *sname = fread_word(fp);

				if ( (sn=skill_lookup(sname)) < 0 )
				{
				if ( (sn=herb_lookup(sname)) < 0 )
					bug( "Fread_char: unknown skill." );
				else
					sn += TYPE_HERB;
				}
				paf->type = sn;
			}

			paf->duration	= fread_number( fp );
			paf->modifier	= fread_number( fp );
			paf->location	= fread_number( fp );
			paf->bitvector	= first_set_bit(fread_number( fp ));
			LINK(paf, ch->first_affect, ch->last_affect, next, prev );
			fMatch = TRUE;
			break;
	    }
	    if ( !str_cmp( word, "AffectEx" ) || !str_cmp( word, "AffectDataEx" ) )
	    {
			AFFECT_DATA *paf;

			if ( preload )
			{
				fMatch = TRUE;
				fread_to_eol( fp );
				break;
			}
			CREATE( paf, AFFECT_DATA, 1 );
			if ( !str_cmp( word, "AffectEx" ) )
			{
				paf->type	= fread_number( fp );
			}
			else
			{
				char *sname = fread_word(fp);

				if ( (sn=skill_lookup(sname)) < 0 )
				{
				if ( (sn=herb_lookup(sname)) < 0 )
					bug( "Fread_char: unknown skill." );
				else
					sn += TYPE_HERB;
				}
				paf->type = sn;
			}

			paf->duration	= fread_number( fp );
			paf->modifier	= fread_number( fp );
			paf->location	= fread_number( fp );
			paf->bitvector	= fread_number( fp );
			LINK(paf, ch->first_affect, ch->last_affect, next, prev );
			fMatch = TRUE;
			break;
	    }
	    if ( !str_cmp( word, "AttrMod"  ) )
	    {
		line = fread_line( fp );
		x1=x2=x3=x4=x5=x6=x7=13;
		sscanf( line, "%d %d %d %d %d %d %d",
		      &x1, &x2, &x3, &x4, &x5, &x6, &x7 );
		ch->mod_str = x1;
		ch->mod_int = x2;
		ch->mod_wis = x3;
		ch->mod_dex = x4;
		ch->mod_con = x5;
		ch->mod_cha = x6;
		ch->mod_lck = x7;
		if (!x7)
		ch->mod_lck = 0;
		fMatch = TRUE;
		break;
	    }

            if ( !str_cmp( word, "Alias" ) )
            {
                ALIAS_DATA *pal;
                
                if ( preload )
                {
                    fMatch = TRUE;
                    fread_to_eol( fp );
                    break;
                }
                CREATE( pal, ALIAS_DATA, 1 );
                
                pal->name	= fread_string_nohash( fp );
                pal->cmd	= fread_string_nohash( fp );
                LINK(pal, ch->pcdata->first_alias, ch->pcdata->last_alias, next, prev );
                fMatch = TRUE;
                break;
            }

	    if ( !str_cmp( word, "AttrPerm" ) )
	    {
		line = fread_line( fp );
		x1=x2=x3=x4=x5=x6=x7=0;
		sscanf( line, "%d %d %d %d %d %d %d",
		      &x1, &x2, &x3, &x4, &x5, &x6, &x7 );
		ch->perm_str = x1;
		ch->perm_int = x2;
		ch->perm_wis = x3;
		ch->perm_dex = x4;
		ch->perm_con = x5;
		ch->perm_cha = x6;
		ch->perm_lck = x7;
		if (x7 == 0)
		  ch->perm_lck = 13;
		fMatch = TRUE;
		break;
	    }
	    KEY( "AuthedBy",	ch->pcdata->authed_by,	fread_string( fp ) );
	    break;

	case 'B':
	    KEY( "Bamfin",	ch->pcdata->bamfin,	fread_string_nohash( fp ) );
	    KEY( "Bamfout",	ch->pcdata->bamfout,	fread_string_nohash( fp ) );
	    KEY( "Bestowments", ch->pcdata->bestowments, fread_string_nohash( fp ) );
	    KEY( "Bio",		ch->pcdata->bio,	fread_string( fp ) );
	    KEY( "Bank",	ch->pcdata->bank,	fread_pfile_integer<long>( fp ) );
	    KEY( "BountyTxn", ch->pcdata->bounty_transaction, fread_word( fp ) );
	    break;

	case 'C':
	    if ( !str_cmp( word, "Clan" ) )
	    {
		ch->pcdata->clan_name = fread_string( fp );

		if ( !preload
		&&   ch->pcdata->clan_name[0] != '\0'
		&& ( ch->pcdata->clan = get_clan( ch->pcdata->clan_name )) == NULL )
		{
		  buf = str_printf("Warning: the organization %s no longer exists, and therefore you no longer\nbelong to that organization.\n",
		           ch->pcdata->clan_name );
		  send_to_char( buf, ch );
		  STRFREE( ch->pcdata->clan_name );
		  remove_member(ch);
		  ch->pcdata->clan_name = STRALLOC( "" );
		}
		else
		  update_member(ch);
		fMatch = TRUE;
		break;
	    }
	    KEY( "Clones",	ch->pcdata->clones,		fread_number( fp ) );
//      KEY(  "Commandgroup", ch->pcdata->commandgroup,     fread_number( fp ) );
		if ( !str_cmp(word, "Channels") )
		{
			fread_bitset(fp, ch->channels);
			fMatch = TRUE;
			break;
		}
		if ( !str_cmp( word, "CommandGroup" ) )
		{
			int legacy = fread_number(fp);
//			fprintf(stderr,"DEBUG: old flags read = %d\r\n", legacy);
			ch->pcdata->commandgroup.reset();  // ensure clean state
			ch->pcdata->commandgroup = int_to_bitset(legacy);
			fMatch = TRUE;
			break;
		}			
		if ( !str_cmp(word, "CommandGroupEx") )
		{
			fread_bitset(fp, ch->pcdata->commandgroup);
			fMatch = TRUE;
			break;
		}				
	    if ( !str_cmp( word, "Condition" ) )
	    {
		line = fread_line( fp );
		sscanf( line, "%d %d %d %d",
		      &x1, &x2, &x3, &x4 );
		ch->pcdata->condition[0] = x1;
		ch->pcdata->condition[1] = x2;
		ch->pcdata->condition[2] = x3;
		ch->pcdata->condition[3] = x4;
		fMatch = TRUE;
		break;
	    }

	    break;

	case 'D':
	    KEY( "Damroll",	ch->damroll,		fread_number( fp ) );
		if ( !str_cmp( word, "Deaf" ) ) // Loading legacy 'deaf' variable into the new channels BitSet.  Bits are flipped!
		{
			const auto deaf = static_cast<uint32_t>( fread_number( fp ) );
			ch->channels.reset();  // clear all bits first

			for ( int i = 0; i < CHANNEL_MAX; ++i )
			{
				if ( ((deaf >> i) & 1U) == 0 )
					BV_SET_BIT(ch->channels, i);
			}
			fMatch = TRUE;
			break;
		}		
	    //KEY( "Deaf",	ch->deaf,		fread_number( fp ) );

	    KEY( "Description",	ch->description,	fread_string( fp ) );
	    if ( !str_cmp( word, "Druglevel"  ) )
	    {
		line = fread_line( fp );
		x0=x1=x2=x3=x4=x5=x6=x7=x8=x9=0;
		sscanf( line, "%d %d %d %d %d %d %d %d %d %d",
		      &x0, &x1, &x2, &x3, &x4, &x5, &x6, &x7, &x8, &x9 );
		ch->pcdata->drug_level[0] = x0;
		ch->pcdata->drug_level[1] = x1;
		ch->pcdata->drug_level[2] = x2;
		ch->pcdata->drug_level[3] = x3;
		ch->pcdata->drug_level[4] = x4;
		ch->pcdata->drug_level[5] = x5;
		ch->pcdata->drug_level[6] = x6;
		ch->pcdata->drug_level[7] = x7;
		ch->pcdata->drug_level[8] = x8;
		ch->pcdata->drug_level[9] = x9;
		fMatch = TRUE;
		break;
	    }
	    break;

	/* 'E' was moved to after 'S' */
        case 'F':
			if ( !str_cmp( word, "Flags" ) )
			{
				int legacy = fread_number(fp);
				fprintf(stderr,"DEBUG: flags read = %d\r\n", legacy);
				ch->pcdata->flags.reset();  // ensure clean state
				ch->pcdata->flags = int_to_bitset(legacy);
				fMatch = TRUE;
				break;
			}
			if ( !str_cmp(word, "FlagsEx") )
			{
				fread_bitset(fp, ch->pcdata->flags);
				fMatch = TRUE;
				break;
			}
				if ( !str_cmp( word, "Force" ) )
			{
				line = fread_line( fp );
				x1=x2=x3=x4=x5=x6=0;
				sscanf( line, "%d %d %d %d",
					&x1, &x2, &x3, &x4);
				ch->perm_frc = x1;
				ch->mod_frc = x2;
				ch->mana = x3;
				ch->max_mana = x4;
				fMatch = TRUE;
				break;
			}
            break;

	case 'G':
	    KEY( "Glory",       ch->pcdata->quest_curr, fread_number( fp ) );
	    KEY( "Gold",	ch->gold,		fread_number( fp ) );
	    /* temporary measure */
	    if ( !str_cmp( word, "Guild" ) )
	    {
		ch->pcdata->clan_name = fread_string( fp );

		if ( !preload
		&&   ch->pcdata->clan_name[0] != '\0'
		&& ( ch->pcdata->clan = get_clan( ch->pcdata->clan_name )) == NULL )
		{
		  buf = str_printf("Warning: the organization %s no longer exists, and therefore you no longer\nbelong to that organization.\n",
		           ch->pcdata->clan_name );
		  send_to_char( buf, ch );
		  STRFREE( ch->pcdata->clan_name );
		  ch->pcdata->clan_name = STRALLOC( "" );
		}
		fMatch = TRUE;
		break;
	    }
            break;

	case 'H':
	    if ( !str_cmp( word, "HomeGeneration" ) )
	    {
	        const int generation = fread_pfile_integer<int>( fp );
	        ch->pcdata->home_generation = generation > 0 ? generation : -1;
	        fMatch = TRUE;
	        break;
	    }
	    if ( !str_cmp(word, "Helled") )
	    {
	      ch->pcdata->release_date = fread_pfile_integer<time_t>( fp );
	      ch->pcdata->helled_by = fread_string(fp);
	      if ( ch->pcdata->release_date < current_time )
	      {
	        STRFREE(ch->pcdata->helled_by);
	        ch->pcdata->helled_by = NULL;
	        ch->pcdata->release_date = 0;
	        ch->pcdata->jail_vnum = 0;
	      }
	      fMatch = TRUE;
	      break;
	    }

	    KEY( "Hitroll",	ch->hitroll,		fread_number( fp ) );
	    KEY( "Homepage",	ch->pcdata->homepage,	fread_string_nohash( fp ) );

	    if ( !str_cmp( word, "HpManaMove" ) )
	    {
		line = fread_line( fp );
		x1=x2=x3=x4=x5=x6=0;
		sscanf( line, "%d %d %d %d %d %d",
		      &x1, &x2, &x3, &x4, &x5, &x6 );
		ch->hit = x1;
		ch->max_hit = x2;
		ch->move = x5;
		ch->max_move = x6;
                if ( x4 >= 100 )
                {
                   ch->perm_frc = number_range( 1 , 20 );
                   ch->max_mana = x4;
                   ch->mana     = x4;
                }
                else if ( x4 >= 10 )
                {
                   ch->perm_frc = 1;
		   ch->max_mana = x4;
		}
		fMatch = TRUE;
		break;
	    }
	    
	    break;

	case 'I':
	    KEY( "IllegalPK",	ch->pcdata->illegal_pk,	fread_number( fp ) );
//	    KEY( "Immune",	ch->immune,		fread_number( fp ) );
		if ( !str_cmp( word, "Immune" ) )
		{
			int legacy = fread_number(fp);
			fprintf(stderr,"DEBUG: flags read = %d\r\n", legacy);
			ch->immune.reset();  // ensure clean state
			ch->immune = int_to_bitset(legacy);
			fMatch = TRUE;
			break;
		}
		if ( !str_cmp(word, "ImmuneEx") )
		{
			fread_bitset(fp, ch->immune);
			fMatch = TRUE;
			break;
		}
		break;
        case 'J':
            KEY( "Jailvnum",    ch->pcdata->jail_vnum,          fread_number( fp ) );
            break;

	case 'K':
	    if ( !str_cmp( word, "Killed" ) )
	    {
		fMatch = TRUE;
		if ( killcnt >= MAX_KILLTRACK )
		    bug( "fread_char: killcnt (%d) >= MAX_KILLTRACK", killcnt );
		else
		{
		    ch->pcdata->killed[killcnt].vnum    = fread_number( fp );
		    ch->pcdata->killed[killcnt++].count = fread_number( fp );
		}
	    }
	    break;

	case 'L':
	    if ( !str_cmp(word, "Lastplayed") )
	    {
	      lastplayed = fread_pfile_integer<time_t>( fp );
	      fMatch = TRUE;
	      break;
	    }
	    KEY( "LongDescr",	ch->long_descr,		fread_string( fp ) );
	    if ( !str_cmp( word, "Languages" ) )
	    {
	    	ch->speaks = int_to_bitset(fread_number( fp ));
	    	ch->speaking = fread_number( fp );
	    	fMatch = TRUE;
	    }
	    break;

	case 'M':
	    KEY( "MainAbility",	ch->main_ability,		fread_number( fp ) );
	    KEY( "MDeaths",	ch->pcdata->mdeaths,	fread_number( fp ) );
	    KEY( "Mentalstate", ch->mental_state,	fread_number( fp ) );
	    KEY( "MGlory",      ch->pcdata->quest_accum,fread_number( fp ) );
	    KEY( "Minsnoop",	ch->pcdata->min_snoop,	fread_number( fp ) );
	    KEY( "MKills",	ch->pcdata->mkills,	fread_number( fp ) );
	    KEY( "Mobinvis",	ch->mobinvis,		fread_number( fp ) );
	    if ( !str_cmp( word, "MobRange" ) )
	    {
		ch->pcdata->m_range_lo = fread_number( fp );
		ch->pcdata->m_range_hi = fread_number( fp );
		fMatch = TRUE;
	    }
	    break;

	case 'N':
	    KEY( "Nextquest",	ch->nextquest,	fread_number( fp ) );

	    if ( !str_cmp( word, "Name" ) )
	    {
		/*
		 * Name already set externally.
		 */
		fread_to_eol( fp );
		fMatch = TRUE;
		break;
	    }
	    break;

	case 'O':
	    KEY( "Outcast_time", ch->pcdata->outcast_time, fread_pfile_integer<long>( fp ) );
	    if ( !str_cmp( word, "ObjRange" ) )
	    {
		ch->pcdata->o_range_lo = fread_number( fp );
		ch->pcdata->o_range_hi = fread_number( fp );
		fMatch = TRUE;
	    }
	    break;

	case 'P':
	    KEY( "Pagerlen",	ch->pcdata->pagerlen,	fread_number( fp ) );
            if ( !str_cmp( word, "Password" ) )
            {
                if ( ch->pcdata->pwd )
                    STR_DISPOSE( ch->pcdata->pwd );
                ch->pcdata->pwd = fread_string_nohash( fp );
                if ( password_offset )
                {
                    const long end = ftell( fp );
                    const long size = static_cast<long>( strlen( ch->pcdata->pwd ) ) + 1;
                    *password_offset = !read_password && end >= size ? end - size : -1;
                }
                read_password = true;
                fMatch = TRUE;
                break;
            }
	    KEY( "PDeaths",	ch->pcdata->pdeaths,	fread_number( fp ) );
	    KEY( "PKills",	ch->pcdata->pkills,	fread_number( fp ) );
	    KEY( "Played",	ch->pcdata->played,	fread_pfile_integer<long long>( fp ) );
	    KEY( "Position",	ch->position,		fread_number( fp ) );
//	    KEY( "Practice",	extra,		fread_number( fp ) );
	    if ( !str_cmp(word, "Prompt") )
	    {
		if (ch->pcdata->prompt)
		    STRFREE(ch->pcdata->prompt);
		ch->pcdata->prompt = fread_string(fp);
		fMatch = TRUE;
		break;
	    }
	    if (!str_cmp ( word, "PTimer" ) )
	    {
		add_timer( ch , TIMER_PKILLED, fread_number(fp), NULL, 0 );	
		fMatch = TRUE;
		break;
	    }
	    if ( !str_cmp( word, "PlrHome" ) )
	    {
		ch->plr_home = get_room_index( fread_number( fp ) );
		if ( !ch->plr_home )
		    ch->plr_home = NULL;
		fMatch = TRUE;
		break;
	    }
	    break;

	case 'Q':
	    KEY( "Questpoints",        ch->questpoints,		fread_number( fp ) );
	    break;

	case 'R':
	    if ( !str_cmp( word, "Race" ) )
	    {
		const int race = fread_number( fp );
		ch->race = race >= 0 && race < MAX_RACE ? race : RACE_HUMAN;
		fMatch = TRUE;
		break;
	    }
	    KEY( "Rank",        ch->pcdata->rank,	fread_string_nohash( fp ) );
//	    KEY( "Resistant",	ch->resistant,		fread_number( fp ) );
	    KEY( "Restore_time",ch->pcdata->restore_time, fread_pfile_integer<long>( fp ) );
		if ( !str_cmp( word, "Resistant" ) )
		{
			int legacy = fread_number(fp);
			fprintf(stderr,"DEBUG: flags read = %d\r\n", legacy);
			ch->resistant.reset();  // ensure clean state
			ch->resistant = int_to_bitset(legacy);
			fMatch = TRUE;
			break;
		}
		if ( !str_cmp(word, "ResistantEx") )
		{
			fread_bitset(fp, ch->resistant);
			fMatch = TRUE;
			break;
		}
	    if ( !str_cmp( word, "Room" ) )
	    {
		ch->in_room = get_room_index( fread_number( fp ) );
		if ( !ch->in_room )
		    ch->in_room = get_room_index( ROOM_VNUM_LIMBO );
		fMatch = TRUE;
		break;
	    }
	    if ( !str_cmp( word, "RoomRange" ) )
	    {
		ch->pcdata->r_range_lo = fread_number( fp );
		ch->pcdata->r_range_hi = fread_number( fp );
		fMatch = TRUE;
	    }
	    break;

	case 'S':
        KEY( "Salary",      ch->pcdata->salary,		fread_number( fp ) );
	    KEY( "Salary_time",ch->pcdata->salary_date, fread_pfile_integer<long>( fp ) );
	    KEY( "Sex",		ch->sex,		fread_number( fp ) );
	    KEY( "ShortDescr",	ch->short_descr,	fread_string( fp ) );
	    //KEY( "Susceptible",	ch->susceptible,	fread_number( fp ) );
		if ( !str_cmp( word, "Susceptible" ) )
		{
			int legacy = fread_number(fp);
			fprintf(stderr,"DEBUG: flags read = %d\r\n", legacy);
			ch->susceptible.reset();  // ensure clean state
			ch->susceptible = int_to_bitset(legacy);
			fMatch = TRUE;
			break;
		}
		if ( !str_cmp(word, "SusceptibleEx") )
		{
			fread_bitset(fp, ch->susceptible);
			fMatch = TRUE;
			break;
		}		
        KEY( "Speaking",      ch->speaking,		fread_number( fp ) );
	    if ( !str_cmp( word, "Speaks" ) )
	    {
			fread_bitset(fp,ch->speaks);
			fMatch = TRUE;
			break;
	    }
		
	    if ( !str_cmp( word, "SavingThrow" ) )
	    {
		ch->saving_wand 	= fread_number( fp );
		ch->saving_poison_death = ch->saving_wand;
		ch->saving_para_petri 	= ch->saving_wand;
		ch->saving_breath 	= ch->saving_wand;
		ch->saving_spell_staff 	= ch->saving_wand;
		fMatch = TRUE;
		break;
	    }

	    if ( !str_cmp( word, "SavingThrows" ) )
	    {
		ch->saving_poison_death = fread_number( fp );
		ch->saving_wand 	= fread_number( fp );
		ch->saving_para_petri 	= fread_number( fp );
		ch->saving_breath 	= fread_number( fp );
		ch->saving_spell_staff 	= fread_number( fp );
		fMatch = TRUE;
		break;
	    }

	    if ( !str_cmp( word, "Site" ) )
	    {
		buf = str_printf("Last connected from: %s\n", fread_word( fp ) );
		send_to_char( buf, ch );
		fMatch = TRUE;
		break;
	    }

	    if ( !str_cmp( word, "Skill" ) )
	    {
		const int value = fread_number( fp );
		if ( file_version < 3 )
		    sn = skill_lookup( fread_word( fp ) );
		else
		    sn = bsearch_skill_exact( fread_word( fp ), gsn_first_skill, gsn_first_weapon-1 );
		if ( sn < 0 )
		    bug( "Fread_char: unknown skill." );
		else
		    ch->pcdata->learned[sn] = value;
		fMatch = TRUE;
		break;
	    }

	    if ( !str_cmp( word, "Spell" ) )
	    {
		const int value = fread_number( fp );
		sn = bsearch_skill_exact( fread_word( fp ), gsn_first_spell, gsn_first_skill-1 );
		if ( sn < 0 )
		    bug( "Fread_char: unknown spell." );
		else
		    ch->pcdata->learned[sn] = value;
		fMatch = TRUE;
	    }
	    break;

	case 'E':
	    if ( !str_cmp( word, "End" ) )
	    {
		if (!ch->short_descr)
		  ch->short_descr	= STRALLOC( "" );
		if (!ch->long_descr)
		  ch->long_descr	= STRALLOC( "" );
		if (!ch->description)
		  ch->description	= STRALLOC( "" );
		if (!ch->pcdata->pwd)
		  ch->pcdata->pwd	= str_dup( "" );
		if (!ch->pcdata->email)
		  ch->pcdata->email	= str_dup( "" );
		if (!ch->pcdata->bamfin)
		  ch->pcdata->bamfin	= str_dup( "" );
		if (!ch->pcdata->bamfout)
		  ch->pcdata->bamfout	= str_dup( "" );
		if (!ch->pcdata->bio)
		  ch->pcdata->bio	= STRALLOC( "" );
		if (!ch->pcdata->rank)
		  ch->pcdata->rank	= str_dup( "" );
		if (!ch->pcdata->bestowments)
		  ch->pcdata->bestowments = str_dup( "" );
		if (!ch->pcdata->title)
		  ch->pcdata->title	= STRALLOC( "" );
		if (!ch->pcdata->homepage)
		  ch->pcdata->homepage	= str_dup( "" );
		if (!ch->pcdata->authed_by)
		  ch->pcdata->authed_by = STRALLOC( "" );
		if (!ch->pcdata->prompt )
		  ch->pcdata->prompt	= STRALLOC( "" );
		ch->editor		= NULL;
		killcnt = URANGE( 2, ((ch->top_level+3) * MAX_KILLTRACK)/LEVEL_AVATAR, MAX_KILLTRACK );
		if ( killcnt < MAX_KILLTRACK )
		  ch->pcdata->killed[killcnt].vnum = 0;
		{
		 int ability;
		 for ( ability = 0 ; ability < MAX_ABILITY ; ability++ )
		 {
		   if ( ch->skill_level[ability] == 0 )
		       ch->skill_level[ability] = 1;
		 }
		}
		if ( !IS_IMMORTAL( ch ) && !ch->speaking )
		/*	ch->speaking = LANG_COMMON;      */
			ch->speaking = race_table[ch->race].language;
		if ( IS_IMMORTAL( ch ) )
		{
			ch->speaks = make_all_languages();
			if ( ch->speaking == 0 )
				ch->speaking = LANG_DIVINE;
		}
		if ( !ch->pcdata->prompt )
		  ch->pcdata->prompt = STRALLOC("");
		  
		if ( lastplayed > 0 && current_time > lastplayed )
		{
		   const auto raw_gain = static_cast<long long>( current_time - lastplayed ) / 60;
		   const int hitgain = static_cast<int>( std::min<long long>( raw_gain, INT_MAX ) );
		   ch->hit = std::clamp<long long>( ch->hit + static_cast<long long>( hitgain ),
		                                  1, std::max<int>( 1, ch->max_hit ) );
		   ch->move = std::clamp<long long>( ch->move + static_cast<long long>( hitgain ),
		                                   1, std::max<int>( 1, ch->max_move ) );
		   if ( ch->skill_level[FORCE_ABILITY] > 1 )
		      ch->mana = std::clamp<long long>( ch->mana + static_cast<long long>( hitgain ),
		                                      0, std::max<int>( 0, ch->max_mana ) );
		   better_mental_state( ch , hitgain );
		}
                for ( sn = 0; sn < top_sn; sn++ )
		{       
	    	   if ( !skill_table[sn]->name )
		      break;

            	   if ( skill_table[sn]->guild < 0 || skill_table[sn]->guild >= MAX_ABILITY )
                      continue;
		
		   if ( ch->pcdata->learned[sn] > 0 && ch->skill_level[skill_table[sn]->guild] < skill_table[sn]->min_level )
                      ch->pcdata->learned[sn] = 0;
		}
		return;
	    }
	    KEY( "Email",	ch->pcdata->email,	fread_string_nohash( fp ) );
	    break;

	case 'T':

	    if ( !str_cmp(word, "Targ") )
	    {
		if (ch->pcdata->target)
		    STRFREE(ch->pcdata->target);
		ch->pcdata->target = fread_string(fp);
		fMatch = TRUE;
		break;
	    }
	    KEY( "Toplevel",	ch->top_level,		fread_number( fp ) );
	    if ( !str_cmp( word, "Tongue" ) )
	    {
		const int value = fread_number( fp );
		sn = bsearch_skill_exact( fread_word( fp ), gsn_first_tongue, gsn_top_sn-1 );
		if ( sn < 0 )
		    bug( "Fread_char: unknown tongue." );
		else
		    ch->pcdata->learned[sn] = value;
		fMatch = TRUE;
		break;
	    }
	    KEY( "Trust", ch->trust, fread_number( fp ) );
            /* Let no character be trusted higher than one below maxlevel -- Narn */
	    ch->trust = UMIN( ch->trust, MAX_LEVEL - 1 );

	    if ( !str_cmp( word, "Title" ) )
	    {
		ch->pcdata->title = fread_string( fp );
		if ( isalpha(ch->pcdata->title[0])
		||   isdigit(ch->pcdata->title[0]) )
		{
		    buf = ch->pcdata->title;
		    if ( ch->pcdata->title )
		      STRFREE( ch->pcdata->title );
		    ch->pcdata->title = STRALLOC( buf );
		}
		fMatch = TRUE;
		break;
	    }

	    break;

	case 'V':
/*	    if ( !str_cmp( word, "Vnum" ) ) - Removed from FUSS - DV 3-13-26
	    {
		ch->pIndexData = get_mob_index( fread_number( fp ) );
		fMatch = TRUE;
		break;
	    }*/
	    KEY( "Version",	file_version,		fread_number( fp ) );
	    break;

	case 'W':
	    if ( !str_cmp( word, "Weapon" ) )
	    {
		const int value = fread_number( fp );
		sn = bsearch_skill_exact( fread_word( fp ), gsn_first_weapon, gsn_first_tongue-1 );
		if ( sn < 0 )
		    bug( "Fread_char: unknown weapon." );
		else
		    ch->pcdata->learned[sn] = value;
		fMatch = TRUE;
		break;
	    }
	    KEY( "Wimpy",	ch->wimpy,		fread_number( fp ) );
	    KEY( "WizInvis",	ch->pcdata->wizinvis,	fread_number( fp ) );
		if ( !str_cmp( word, "Wanted" ) )
		{
			int legacy = fread_number(fp);
//			fprintf(stderr,"DEBUG: old flags read = %d\r\n", legacy);
			ch->pcdata->wanted_flags.reset();  // ensure clean state
			ch->pcdata->wanted_flags = int_to_bitset(legacy);
			fMatch = TRUE;
			break;		
		}
		if ( !str_cmp( word, "WantedEx" ) )
		{
			fread_bitset(fp, ch->pcdata->wanted_flags);
			fMatch = TRUE;
			break;		
		}		
//	    KEY( "Wanted",	ch->pcdata->wanted_flags,  fread_number( fp ) );

	    break;
	}

	if ( !fMatch )
	{
	    bug( "Fread_char: no match: %s", word );
	}
    }
}


void fread_obj(GameContext *game, CHAR_DATA *ch, FILE *fp, sh_int os_type, ObjectNest &nest)
{
    fread_obj_versioned(game, ch, fp, os_type, SAVEVERSION, nest);
}

static void fread_obj_versioned(GameContext *game, CHAR_DATA *ch, FILE *fp, sh_int os_type,
                               int file_version, ObjectNest &nest, EquipmentSlots *equipment)
{
    OBJ_DATA *obj;
    const char *word;
    int iNest;
    bool fMatch;
    bool fNest;
    bool fVnum;
    bool has_objflags = false;
    bool has_values = false;
    ROOM_INDEX_DATA *room = NULL;

    CREATE( obj, OBJ_DATA, 1 );
	obj->game = game;
    obj->count		= 1;
    obj->wear_loc	= -1;
    obj->weight		= 1;
    obj->armed_by	= STRALLOC( "" );

    fNest		= TRUE;		/* Requiring a Nest 0 is a waste */
    fVnum		= TRUE;
    iNest		= 0;

    for ( ; ; )
    {
      word = ( feof( fp ) ? "End" : fread_word( fp ) );

      if( word[0] == '\0' )
      {
         bug( "%s: EOF encountered reading file!", __FUNCTION__ );
         word = "End";
      }
      fMatch = FALSE;

	switch ( UPPER(word[0]) )
	{
	case '*':
	    fMatch = TRUE;
	    fread_to_eol( fp );
	    break;

	case 'A':
	    if ( !str_cmp( word, "Affect" ) || !str_cmp( word, "AffectData" ) )
	    {
		AFFECT_DATA *paf;
		int pafmod;

		CREATE( paf, AFFECT_DATA, 1 );
		if ( !str_cmp( word, "Affect" ) )
		{
		    paf->type	= fread_number( fp );
		}
		else
		{
		    int sn;

		    sn = skill_lookup( fread_word( fp ) );
		    if ( sn < 0 )
			bug( "Fread_obj: unknown skill." );
		    else
			paf->type = sn;
		}
		paf->duration	= fread_number( fp );
		pafmod		= fread_number( fp );
		paf->location	= fread_number( fp );
		paf->bitvector	= first_set_bit(fread_number( fp ));
		if ( paf->location == APPLY_WEAPONSPELL
		||   paf->location == APPLY_WEARSPELL
		||   paf->location == APPLY_REMOVESPELL
		||   paf->location == APPLY_STRIPSN )
		  paf->modifier		= slot_lookup( pafmod );
		else
		  paf->modifier		= pafmod;
		LINK(paf, obj->first_affect, obj->last_affect, next, prev );
		fMatch				= TRUE;
		break;
	    }
	    if ( !str_cmp( word, "AffectEx" ) || !str_cmp( word, "AffectDataEx" ) )
	    {
		AFFECT_DATA *paf;
		int pafmod;

		CREATE( paf, AFFECT_DATA, 1 );
		if ( !str_cmp( word, "AffectEx" ) )
		{
		    paf->type	= fread_number( fp );
		}
		else
		{
		    int sn;

		    sn = skill_lookup( fread_word( fp ) );
		    if ( sn < 0 )
			bug( "Fread_obj: unknown skill." );
		    else
			paf->type = sn;
		}
		paf->duration	= fread_number( fp );
		pafmod		= fread_number( fp );
		paf->location	= fread_number( fp );
		paf->bitvector	= fread_number( fp );
		if ( paf->location == APPLY_WEAPONSPELL
		||   paf->location == APPLY_WEARSPELL
		||   paf->location == APPLY_REMOVESPELL
		||   paf->location == APPLY_STRIPSN )
		  paf->modifier		= slot_lookup( pafmod );
		else
		  paf->modifier		= pafmod;
		LINK(paf, obj->first_affect, obj->last_affect, next, prev );
		fMatch				= TRUE;
		break;
	    }		
	    if ( !str_cmp( word, "Actiondesc" ) )
	    {
		if ( obj->action_desc )
		    STRFREE( obj->action_desc );
		obj->action_desc = fread_string( fp );
		fMatch = TRUE;
	    }
	    break;

	case 'C':
	    KEY( "Cost",	obj->cost,		fread_number( fp ) );
	    KEY( "Count",	obj->count,		fread_number( fp ) );
	    break;

	case 'D':
	    KEY( "Description",	obj->description,	fread_string( fp ) );
	    break;

	case 'E':
		if ( !str_cmp( word, "ExtraFlags" ) )
		{
			int legacy = fread_number(fp);
			int_into_bitset_offset(obj->objflags, legacy, ITEM_FIRST);
			fMatch = TRUE;
			break;
		}
	    if ( !str_cmp( word, "ExtraDescr" ) )
	    {
		EXTRA_DESCR_DATA *ed;

		CREATE( ed, EXTRA_DESCR_DATA, 1 );
		ed->keyword		= fread_string( fp );
		ed->description		= fread_string( fp );
		LINK(ed, obj->first_extradesc, obj->last_extradesc, next, prev );
		++top_ed;
		fMatch 				= TRUE;
		break;
	    }

	    if ( !str_cmp( word, "End" ) )
	    {
		if ( !fNest || !fVnum || !obj->pIndexData )
		{
			if( obj->name )
				bug( "%s: %s incomplete object.", __FUNCTION__, obj->name );
			else
				bug( "%s: incomplete object.", __FUNCTION__ );
			free_obj( obj );
			return;
		}
		else
		{
		    sh_int wear_loc = obj->wear_loc;

		    if ( os_type == OS_CORPSE && !room )
		    {
			bug( "Fread_obj: Corpse without room" );
			room = get_room_index( ROOM_VNUM_LIMBO );
			if ( !room )
			{
			    free_obj( obj );
			    return;
			}
		    }

		    if ( !obj->name )
			obj->name = QUICKLINK( obj->pIndexData->name );
		    if ( !obj->description )
			obj->description = QUICKLINK( obj->pIndexData->description );
		    if ( !obj->short_descr )
			obj->short_descr = QUICKLINK( obj->pIndexData->short_descr );
		    if ( !obj->action_desc )
			obj->action_desc = QUICKLINK( obj->pIndexData->action_desc );
		    if ( !has_objflags && has_values )
		    {
			if ( obj->item_type == ITEM_WEAPON )
			{
			    int wtype = obj->value[3];
			    if ( wtype >= 0 && static_cast<size_t>(wtype) + WEAPON_FIRST < WEAPON_MAX )
				set_weapon_flag( obj->objflags, WEAPON_FIRST + static_cast<size_t>(wtype) );
			    else
			    {
				bug( "Fread_obj: vnum %d bad weapon type value[3] = %d.",
				     obj->pIndexData->vnum, wtype );
				set_weapon_flag( obj->objflags, WEAPON_FIRST );
			    }
			}
			else if ( obj->item_type == ITEM_PIPE && obj->value[3] != 0 )
			{
			    uint32_t uflags = static_cast<uint32_t>(obj->value[3]);
			    for ( size_t bit = 1; bit < PIPE_MAX - PIPE_FIRST; ++bit )
				if ( uflags & (1U << bit) )
				    obj->objflags.set( PIPE_FIRST + bit );
			}
		    }
		    LINK(obj, first_object, last_object, next, prev );
		    obj->pIndexData->count += obj->count;
		    if ( !obj->serial )
		    {
			cur_obj_serial = UMAX((cur_obj_serial + 1 ) & (BV30-1), 1);
			obj->serial = obj->pIndexData->serial = cur_obj_serial;
		    }
		    if ( fNest )
		      nest[iNest] = obj;
		    numobjsloaded += obj->count;
		    ++physicalobjects;
		    if ( file_version > 1 || !is_valid_wear_location( obj->wear_loc ) )
		      obj->wear_loc = -1;
		    /* Corpse saving. -- Altrag */
		    if ( os_type == OS_CORPSE )
			obj = obj_to_room( obj, room );
		    else if ( iNest == 0 || nest[iNest] == NULL )
		    {
			OBJ_DATA **slot = nullptr;

			if (equipment && ch && file_version > 1 && is_valid_wear_location(wear_loc))
			{
			    auto &layers = (*equipment)[wear_loc];
			    const auto empty_slot = std::find(layers.begin(), layers.end(), nullptr);
			    if (empty_slot == layers.end())
				bug("Fread_obj: too many layers %d", wear_loc);
			    else
			    {
				slot = &*empty_slot;
				*slot = obj;
			    }
			}
			if ( !ch )
			{
			   bug( "Fread_obj: NULL ch for OS_CARRY object vnum %d.",
				obj->pIndexData ? obj->pIndexData->vnum : 0 );
			   extract_obj( obj );
			   obj = NULL;
			}
			else
			{
			   obj = obj_to_char(obj, ch, equipment);
			   if (slot)
			      *slot = (obj && obj->carried_by == ch) ? obj : nullptr;
			}
		    }
		    else
		    {
			if ( nest[iNest-1] )
			{
			   separate_obj(nest[iNest-1]);
			   obj = obj_to_obj(obj, nest[iNest-1]);
			}
			else
			{
			   bug( "Fread_obj: nest layer missing %d", iNest-1 );
			   extract_obj( obj );
			   obj = NULL;
			}
		    }
		    if ( fNest )
		      nest[iNest] = obj;
		    return;
		}
	    }
	    break;

	case 'I':
	    KEY( "ItemType",	obj->item_type,		fread_number( fp ) );
	    break;

	case 'L':
	    KEY( "Level",	obj->level,		fread_number( fp ) );
	    break;

	case 'N':
	    KEY( "Name",	obj->name,		fread_string( fp ) );

	    if ( !str_cmp( word, "Nest" ) )
	    {
		iNest = fread_number( fp );
		if ( iNest < 0 || iNest >= MAX_NEST )
		{
		    bug( "Fread_obj: bad nest %d.", iNest );
		    iNest = 0;
		    fNest = FALSE;
		}
		fMatch = TRUE;
	    }
	    break;
	case 'O':
	    if ( !str_cmp( word, "Objflags" ) )
	    {
			fread_bitset(fp, obj->objflags);
			has_objflags = true;
			fMatch = TRUE;
	    }
	    break;	    
	case 'R':
	    KEY( "Room", room, get_room_index(fread_number(fp)) );
	    break;

	case 'S':
	    KEY( "ShortDescr",	obj->short_descr,	fread_string( fp ) );

	    if ( !str_cmp( word, "Spell" ) )
	    {
		int iValue;
		int sn;

		iValue = fread_number( fp );
		sn     = skill_lookup( fread_word( fp ) );
		if ( iValue < 0 || iValue > 5 )
		    bug( "Fread_obj: bad iValue %d.", iValue );
		else if ( sn < 0 )
		    bug( "Fread_obj: unknown skill." );
		else
		    obj->value[iValue] = sn;
		fMatch = TRUE;
		break;
	    }

	    break;

	case 'T':
	    KEY( "Timer",	obj->timer,		fread_number( fp ) );
	    if ( !str_cmp( word, "Triggerflags" ) )
	    {
			fread_bitset( fp, obj->trig_flags );
			fMatch = TRUE;
			break;
	    }
	    break;

	case 'V':
	    if ( !str_cmp( word, "Values" ) )
	    {
		int x1,x2,x3,x4,x5,x6;
		char *ln = fread_line( fp );

		x1=x2=x3=x4=x5=x6=0;
		sscanf( ln, "%d %d %d %d %d %d", &x1, &x2, &x3, &x4, &x5, &x6 );

		obj->value[0]	= x1;
		obj->value[1]	= x2;
		obj->value[2]	= x3;
		obj->value[3]	= x4;
		obj->value[4]	= x5;
		obj->value[5]	= x6;
		has_values	= true;
		fMatch		= TRUE;
		break;
	    }

	    if ( !str_cmp( word, "Vnum" ) )
	    {
		int vnum;

		vnum = fread_number( fp );
		if ( ( obj->pIndexData = get_obj_index( vnum ) ) == NULL )
		{
		    fVnum = FALSE;
		    bug( "Fread_obj: bad vnum %d.", vnum ); 
		}
		else
		{
		    fVnum = TRUE;
		    obj->cost = obj->pIndexData->cost;
		    obj->weight = obj->pIndexData->weight;
		    obj->item_type = obj->pIndexData->item_type;
		    obj->wear_flags = obj->pIndexData->wear_flags;
		    obj->objflags = obj->pIndexData->objflags;
			obj->trig_flags = obj->pIndexData->trig_flags;
		}
		fMatch = TRUE;
		break;
	    }
	    break;

	case 'W':
//	    KEY( "WearFlags",	obj->wear_flags,	fread_number( fp ) );
		if ( !str_cmp( word, "WearFlags" ) )
		{
			int legacy = fread_number(fp);
//			fprintf(stderr,"DEBUG: flags read = %d\r\n", legacy);
			obj->wear_flags.reset();  // ensure clean state
			obj->wear_flags = int_to_bitset(legacy);
			fMatch = TRUE;
			break;
		}
		if ( !str_cmp(word, "WearFlagsEx") )
		{
			fread_bitset(fp, obj->wear_flags);
			fMatch = TRUE;
			break;
		}		
	    KEY( "WearLoc",	obj->wear_loc,		fread_number( fp ) );
	    KEY( "Weight",	obj->weight,		fread_number( fp ) );
	    break;

	}

	if ( !fMatch )
	{
	    bug( "Fread_obj: no match." );
	    bug( "%s", word );
	    fread_to_eol( fp );
	    free_obj( obj );
	    return;
	}
    }
}

void set_alarm( long seconds )
{
//    alarm( seconds );
	return;
}

/*
 * Based on last time modified, show when a player was last on	-Thoric
 */
void do_last( CHAR_DATA *ch, char *argument )
{
    std::string buf;
    std::string arg;
    std::string name;
    struct stat fst;

    one_argument( argument, arg );
    if ( arg.empty() )
    {
	send_to_char( "Usage: last <playername>\n", ch );
	return;
    }
    name = capitalize(arg);
    buf =str_printf( "%s%c/%s", PLAYER_DIR, tolower(static_cast<unsigned char>(arg[0])), name.c_str() );
    if ( stat( buf.c_str(), &fst ) != -1 )
      buf = str_printf( "%s was last on: %s\n", name.c_str(), ctime( &fst.st_mtime ) );
    else
      buf = str_printf( "%s was not found.\n", name.c_str() );
   send_to_char( buf, ch );
}

void write_corpses( CHAR_DATA *ch, const std::string& name )
{
  std::string namestr = name;
  OBJ_DATA *corpse;
  FILE *fp = NULL;
  
  /* Name and ch support so that we dont have to have a char to save their
     corpses.. (ie: decayed corpses while offline) */
  if ( ch && IS_NPC(ch) )
  {
    bug( "Write_corpses: writing NPC corpse." );
    return;
  }
  if ( ch )
    namestr = ch->name;
  if ( namestr.empty() )
    return;
  /* Go by vnum, less chance of screwups. -- Altrag */
  for ( corpse = first_object; corpse; corpse = corpse->next )
    if ( corpse->pIndexData->vnum == OBJ_VNUM_CORPSE_PC &&
         corpse->in_room != NULL && corpse->value[1] != 1 &&
         corpse->short_descr != NULL && strlen(corpse->short_descr) >= 14 &&
        !str_cmp(corpse->short_descr+14, namestr) )
    {
      if ( !fp )
      {
        char buf[127];
        
        SPRINTF(buf, "%s%s", CORPSE_DIR, capitalize(namestr).c_str());
        if ( !(fp = fopen(buf, "w")) )
        {
          bug( "Write_corpses: Cannot open file." );
          perror(buf);
          return;
        }
      }
      fwrite_obj(ch, corpse, fp, 0, OS_CORPSE);
    }
  if ( fp )
  {
    fprintf(fp, "#END\n\n");
    FCLOSE(fp);
  }
  else
  {
    char buf[127];
    
    SPRINTF(buf, "%s%s", CORPSE_DIR, capitalize(namestr).c_str());
    remove(buf);
  }
  return;
}

void load_corpses( GameContext *game )
{
  DIR *dp;
  struct dirent *de;
  extern FILE *fpArea;
  extern char strArea[MAX_INPUT_LENGTH];
  extern int falling;
  
  if ( !(dp = opendir(CORPSE_DIR)) )
  {
    bug( "Load_corpses: can't open CORPSE_DIR");
    perror(CORPSE_DIR);
    return;
  }

  falling = 1; /* Arbitrary, must be >0 though. */
  while ( (de = readdir(dp)) != NULL )
  {
    if ( de->d_name[0] != '.' )
    {
      SPRINTF(strArea, "%s%s", CORPSE_DIR, de->d_name );
      fprintf(stderr, "Corpse -> %s\n", strArea);
      if ( !(fpArea = fopen(strArea, "r")) )
      {
        perror(strArea);
        continue;
      }
      ObjectNest nest{};
      for ( ; ; )
      {
        char letter;
        char *word;
        
        letter = fread_letter( fpArea );
        if ( letter == '*' )
        {
          fread_to_eol(fpArea);
          continue;
        }
        if ( letter != '#' )
        {
          bug( "Load_corpses: # not found." );
          break;
        }
        word = fread_word( fpArea );
        if ( word[0] == '\0' )
          break;
        if ( !str_cmp(word, "CORPSE" ) )
        {
          nest.fill(nullptr);
          fread_obj(game, NULL, fpArea, OS_CORPSE, nest);
        }
        else if ( !str_cmp(word, "OBJECT" ) )
          fread_obj(game, NULL, fpArea, OS_CARRY, nest);
        else if ( !str_cmp( word, "END" ) )
          break;
        else
        {
          bug( "Load_corpses: bad section." );
          break;
        }
      }
      FCLOSE(fpArea);
    }
  }
  fpArea = NULL;
  SPRINTF(strArea, "$");
  closedir(dp);
  falling = 0;
  return;
}

void load_storerooms( GameContext *game )
{
  DIR *dp;
  struct dirent *de;
  extern FILE *fpArea;
  extern char strArea[MAX_INPUT_LENGTH];
  extern int falling;
  char buf[MAX_INPUT_LENGTH];

if ( !(dp = opendir(STOREROOM_DIR)) )
  {
    bug( "Load_storeroom: can't open STOREROOM_DIR");
    perror(STOREROOM_DIR);
    return;
  }

  falling = 1;
  while ( (de = readdir(dp)) != NULL )
  {
    if ( de->d_name[0] != '.' )
    {
//	    bool found;
	    OBJ_DATA *tobj;
	    ROOM_INDEX_DATA *storeroom;

      SPRINTF(strArea, "%s%s", STOREROOM_DIR, de->d_name );
      fprintf(stderr, "STOREROOM -> %s\n", strArea);
      if ( !(fpArea = fopen(strArea, "r")) )
      {
        perror(strArea);
        continue;
      }

	    storeroom = get_room_index(atoi(de->d_name));
	    if( !storeroom )
	    {
		FCLOSE( fpArea );
		continue;
	    }
  	    
	    if ( !BV_IS_SET( storeroom->room_flags, ROOM_CLANSTOREROOM ) )
	    {
              FCLOSE( fpArea );
              SPRINTF( buf, "%s%d", STOREROOM_DIR, storeroom->vnum );
              remove( buf );
              continue;
            }
  	    
	      
	    if ( !supermob )
	    {
		bug( "load_storerooms: NULL supermob" );
		FCLOSE( fpArea );
		continue;
	    }

	    rset_supermob(storeroom);
	    ObjectNest nest{};

//	    found = TRUE;
	    for ( ; ; )
	    {
		char letter;
		char *word;

		letter = fread_letter( fpArea );
		if ( letter == '*' )
		{
		    fread_to_eol( fpArea );
		    continue;
		}

		if ( letter != '#' )
		{
		    bug( "load_storerooms: # not found." );
		    bug( "%s", de->d_name );
		    break;
		}

		word = fread_word( fpArea );
		if ( word[0] == '\0' )
		  break;
		if ( !str_cmp( word, "OBJECT" ) )	/* Objects	*/
		  fread_obj(game, supermob, fpArea, OS_CARRY, nest);
		else
		if ( !str_cmp( word, "END"    ) )	/* Done		*/
		  break;
		else
		{
		    bug( "load_storerooms: bad section." );
		    bug( "%s", de->d_name );
		    break;
		}
	    }

	    FCLOSE( fpArea );

	    while ( ( tobj = supermob->first_carrying ) != NULL )
	    {
		obj_from_char( tobj );
                if( tobj->item_type != ITEM_MONEY )
		  obj_to_room( tobj, storeroom );
		else
		  extract_obj( tobj );
	    }
	    
	    release_supermob(game);

         }
    }

  fpArea = NULL;
  SPRINTF(strArea, "$");
  closedir(dp);
  falling = 0;
  return;
}

void save_storeroom( ROOM_INDEX_DATA *room )
{
    char strsave[MAX_INPUT_LENGTH];
    FILE *fp;
    OBJ_DATA *contents;

    if ( !room )
    {
	bug( "save_storeroom: null ch!" );
	return;
    }

    SPRINTF( strsave, "%s%d",STOREROOM_DIR, room->vnum );

    if ( ( fp = fopen( strsave, "w" ) ) == NULL )
    {
		perror( strsave );
		bug( "Save_storeroom: fopen" );
		bug( "%s", strsave );
	
    }
    else
    {
		chmod(strsave, S_IRUSR|S_IWUSR | S_IRGRP|S_IWGRP | S_IROTH|S_IWOTH);

       	contents = room->last_content;
       	if (contents)
  	  		fwrite_obj(NULL, contents, fp, 0, OS_CARRY );
		fprintf( fp, "#END\n" );
		FCLOSE( fp );

    }

    return;
}


void load_vendors( GameContext *game )
{
  DIR *dp;
  struct dirent *de;
  extern FILE *fpArea;
  extern char strArea[MAX_INPUT_LENGTH];
  extern int falling;

if ( !(dp = opendir(VENDOR_DIR)) )
  {
    bug( "Load_vendors: can't open VENDOR_DIR");
    perror(VENDOR_DIR);
    return;
  }

  falling = 1;
  while ( (de = readdir(dp)) != NULL )
  {
    if ( de->d_name[0] != '.' )
    {
      CHAR_DATA *mob = NULL;
      SPRINTF(strArea, "%s%s", VENDOR_DIR, de->d_name );
      fprintf(stderr, "Vendor -> %s\n", strArea);
      if ( !(fpArea = fopen(strArea, "r")) )
      {
        perror(strArea);
        continue;
      }
      ObjectNest nest{};
      for ( ; ; )
      {
		  char letter;
        char *word;

        letter = fread_letter( fpArea );
        if ( letter == '*' )
        {
          fread_to_eol(fpArea);
          continue;
        }
        if ( letter != '#' )
        {
          bug( "Load_vendor: # not found." );
          break;
        }
        word = fread_word( fpArea );
        if ( word[0] == '\0' )
          break;
        if ( !strcmp(word, "VENDOR" ) )
          mob = fread_vendor( game, fpArea );
        else if ( !strcmp(word, "OBJECT" ) )
        {
          if ( !mob )
          {
            bug( "Load_vendors: #OBJECT without valid #VENDOR in %s", de->d_name );
            break;
          }
          fread_obj(game, mob, fpArea, OS_CARRY, nest);
        }
        else if ( !strcmp( word, "END" ) )
          break;
        else
        {
          bug( "Load_vendors: bad section: %s", word );
          break;
        }
       }
      FCLOSE(fpArea);
    }
  }
  fpArea = NULL;
  SPRINTF(strArea, "$");
  closedir(dp);
  falling = 0;
  return;
 }
 /*
  * This will write one mobile structure pointed to be fp --Shaddai
  */
 void fwrite_mobile( FILE *fp, CHAR_DATA *mob )
 {
   if ( !IS_NPC( mob ) || !fp )
 	return;
   fprintf( fp, "#MOBILE\n" );
   fprintf( fp, "Vnum	%d\n", mob->pIndexData->vnum );
   if ( mob->in_room )
 	fprintf( fp, "Room	%d\n", 
        		(  mob->in_room == get_room_index( ROOM_VNUM_LIMBO )
         		&& mob->was_in_room )
             		? mob->was_in_room->vnum
             		: mob->in_room->vnum ); 
   if ( str_cmp( mob->name, mob->pIndexData->player_name) )
         fprintf( fp, "Name     %s~\n", mob->name );
   if ( str_cmp( mob->short_descr, mob->pIndexData->short_descr) )
   	fprintf( fp, "Short	%s~\n", mob->short_descr );
   if ( str_cmp( mob->long_descr, mob->pIndexData->long_descr) )
   	fprintf( fp, "Long	%s~\n", mob->long_descr );
   if ( str_cmp( mob->description, mob->pIndexData->description) )
   	fprintf( fp, "Description %s~\n", mob->description );
   fprintf( fp, "Position %d\n", mob->position );
   fwrite_bitset(fp,"FlagsEx",mob->act);
//   fprintf( fp, "Flags %d\n", mob->act );
   if ( mob->first_carrying )
 	fwrite_obj( mob, mob->last_carrying, fp, 0, OS_CARRY );
   fprintf( fp, "EndMobile\n" );
   return;
 }
 
 /*
  * This will read one mobile structure pointer to by fp --Shaddai
  */
 CHAR_DATA *  fread_mobile( GameContext *game, FILE *fp, int file_version )
 {
   CHAR_DATA *mob = NULL;
   const char *word;
   bool fMatch;
   int inroom = 0;
   ROOM_INDEX_DATA *pRoomIndex = NULL;
   ObjectNest nest{};
 
   word   = feof( fp ) ? "EndMobile" : fread_word( fp );
   if ( !strcmp(word, "Vnum") )
   {
     int vnum;
     MOB_INDEX_DATA *pMobIndex;
     
     vnum = fread_number( fp );
     pMobIndex = get_mob_index( vnum );
     if ( !pMobIndex )
     {
 	for ( ; ; ) {
 	  word   = feof( fp ) ? "EndMobile" : fread_word( fp );
 	  /* So we don't get so many bug messages when something messes up
 	   * --Shaddai 
 	   */
 	  if ( word[0] == '\0' || !strcmp( word, "EndMobile" ) )
 		break;
         }
 	bug("Fread_mobile: No index data for vnum %d", vnum );
 	return NULL;
     }
     mob = create_mobile( game, pMobIndex );
     if ( !mob )
     {
 	for ( ; ; ) {
 	  word   = feof( fp ) ? "EndMobile" : fread_word( fp );
 	  if ( word[0] == '\0' || !strcmp( word, "EndMobile" ) )
 		break;
         }
 	bug("Fread_mobile: No index data for vnum %d", vnum );
 	return NULL;
     }
   }
   else
   {
 	for ( ; ; ) {
 	  word   = feof( fp ) ? "EndMobile" : fread_word( fp );
 	  /* So we don't get so many bug messages when something messes up
 	   * --Shaddai 
 	   */
 	  if ( word[0] == '\0' || !strcmp( word, "EndMobile" ) )
 		break;
         }
 	bug("Fread_mobile: Vnum not found" );
 	return NULL;
   }
   for ( ; ;) {
        word   = feof( fp ) ? "EndMobile" : fread_word( fp );
        if ( word[0] == '\0' )
            word = "EndMobile";
        fMatch = FALSE; 
        switch ( UPPER(static_cast<unsigned char>(word[0])) ) {
 	case '*':
            fMatch = TRUE;
            fread_to_eol( fp );
            break;  
 	case '#':
 		if ( !strcmp( word, "#OBJECT" ) )
 		{
			fread_obj_versioned(game, mob, fp, OS_CARRY, file_version, nest);
 			fMatch = TRUE;
 		}
 		break;
 	case 'D':
 		if ( !str_cmp( word, "Description" ) )
 		{
 			if ( mob->description )
 				STRFREE( mob->description );
 			mob->description = fread_string( fp );
 			fMatch = TRUE;
 			break;
 		}
 		break;
 	case 'E':
 		if ( !strcmp( word, "EndMobile" ) )
 		{
 		if ( inroom == 0 )
 			inroom = ROOM_VNUM_TEMPLE;
 		pRoomIndex = get_room_index( inroom );
 		if ( !pRoomIndex )
 			pRoomIndex = get_room_index( ROOM_VNUM_TEMPLE );
 		if ( !pRoomIndex )
 			pRoomIndex = get_room_index( ROOM_VNUM_LIMBO );
 		char_to_room(mob, pRoomIndex);
 		return mob;
 		}
 		break;
  	case 'F':
		if ( !str_cmp( word, "Flags"  ) )
		{
			mob->act = int_to_bitset(fread_number( fp ));
			fMatch = TRUE;
			break;
		}
		if ( !str_cmp( word, "FlagsEx"  ) )
		{
			fread_bitset(fp, mob->act);
			fMatch = TRUE;
			break;
		}	
 		//KEY( "Flags", mob->act, fread_number( fp ));
 		break;
 	case 'L':
 		if ( !str_cmp( word, "Long" ) )
 		{
 			if ( mob->long_descr )
 				STRFREE( mob->long_descr );
 			mob->long_descr = fread_string( fp );
 			fMatch = TRUE;
 			break;
 		}
 		break;
 	case 'N':
 		if ( !str_cmp( word, "Name" ) )
 		{
 			if ( mob->name )
 				STRFREE( mob->name );
 			mob->name = fread_string( fp );
 			fMatch = TRUE;
 			break;
 		}
 		break;
 	case 'P':
 		KEY( "Position", mob->position, fread_number( fp ) );
 		break;
 	case 'R':
 		KEY( "Room",  inroom, fread_number(fp));
 		break;
 	case 'S':
 		if ( !str_cmp( word, "Short" ) )
 		{
 			if ( mob->short_descr )
 				STRFREE( mob->short_descr );
 			mob->short_descr = fread_string( fp );
 			fMatch = TRUE;
 			break;
 		}
 		break;
 	}
 	if ( !fMatch )
 	{
 	   bug ( "Fread_mobile: no match." );
 	   bug ( "%s", word );
 	}
   }
   return NULL;
 }
 
 
 /*
  * This will write in the saved mobile for a char --Shaddai
  */
 void write_char_mobile( CHAR_DATA *ch , char *argument )
 {
   FILE *fp;
   CHAR_DATA *mob;
 
   if ( IS_NPC( ch ) || !ch->pcdata->pet )
 	return;
 
   FCLOSE( fpReserve );
   if ( (fp = fopen( argument, "w")) == NULL )
   {
 	bug( "Write_char_mobile: couldn't open %s for writing!", 
 		argument );
 	fpReserve = fopen( NULL_FILE, "r" );
 	return;
   }
   mob = ch->pcdata->pet;
   BV_REMOVE_BIT( mob->affected_by, AFF_CHARM );
   fwrite_mobile( fp, mob );
   FCLOSE( fp );
   fpReserve = fopen( NULL_FILE, "r" );
   return;
 }
 
 /*
  * This will read in the saved mobile for a char --Shaddai
  */
 void read_char_mobile( GameContext *game, char *argument )
 {
   FILE *fp;
//   CHAR_DATA *mob;
 
   FCLOSE( fpReserve );
   if ( (fp = fopen( argument, "r")) == NULL )
   {
 	bug( "Read_char_mobile: couldn't open %s for reading!", 
 		argument );
 	fpReserve = fopen( NULL_FILE, "r" );
 	return;
   }
   /*mob = */fread_mobile( game, fp );
  FCLOSE( fp );
  fpReserve = fopen( NULL_FILE, "r" );

}

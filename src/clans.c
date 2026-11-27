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
*			     Special clan module			    *
****************************************************************************/

#include <sys/types.h>
#include <sys/stat.h>
#include <errno.h>
#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <climits>
#include <cstdlib>
#include <time.h>
#include "mud.h"

#define MAX_NEST	100

CLAN_DATA * first_clan;
CLAN_DATA * last_clan;

MEMBER_LIST * first_member_list;
MEMBER_LIST * last_member_list;

SENATE_DATA * first_senator;
SENATE_DATA * last_senator;

PLANET_DATA * first_planet;
PLANET_DATA * last_planet;

GUARD_DATA * first_guard;
GUARD_DATA * last_guard;

/* local routines */
void	fread_clan	args( ( CLAN_DATA *clan, FILE *fp ) );
bool	load_clan_file	args( ( GameContext *game, const std::string& clanfile ) );
void	fread_planet	args( ( PLANET_DATA *planet, FILE *fp ) );
bool	load_planet_file	args( ( GameContext *game, const std::string& planetfile ) );
void	save_member_list	args( ( MEMBER_LIST *members_list ) );
static bool save_member_list_file( const CLAN_DATA *clan, const MEMBER_LIST *members_list );
void	show_members		args( ( CHAR_DATA *ch, const std::string& argument, const std::string& format ) );


/*
 * Get pointer to clan structure from clan name.
 */
CLAN_DATA *get_clan( const std::string& name )
{
    CLAN_DATA *clan;
    
    if ( name.empty() )
        return NULL;

    for ( clan = first_clan; clan; clan = clan->next )
       if ( clan->name && !str_cmp( name, clan->name ) )
         return clan;
    return NULL;
}

PLANET_DATA *get_planet( const std::string&name )
{
    PLANET_DATA *planet;
    
    if ( name.empty() )
        return NULL;

    for ( planet = first_planet; planet; planet = planet->next )
       if ( planet->name && !str_cmp( name, planet->name ) )
         return planet;
    return NULL;
}

static bool realm_filename_valid( const std::string &name )
{
    return !name.empty() && isalnum( static_cast<unsigned char>( name.front() ) )
        && name.find_first_not_of( "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" ) == std::string::npos
        && name.find( ".." ) == std::string::npos;
}

template <typename Writer>
static bool write_realm_file( const std::string &path, Writer write )
{
    std::string temporary = path + ".tmp.XXXXXX";
    const int fd = mkstemp( temporary.data() );
    if ( fd < 0 )
    {
        bug( "Cannot open temporary file for %s: %s", path.c_str(), strerror( errno ) );
        return false;
    }

    FILE *fp = fdopen( fd, "wb" );
    if ( !fp )
    {
        bug( "Cannot open temporary stream for %s: %s", path.c_str(), strerror( errno ) );
        close( fd );
        unlink( temporary.c_str() );
        return false;
    }

    const bool complete = write( fp ) && !ferror( fp ) && fflush( fp ) == 0 && fsync( fd ) == 0;
    const bool closed = fclose( fp ) == 0;
    if ( complete && closed && rename( temporary.c_str(), path.c_str() ) == 0 )
        return true;

    bug( "Cannot save %s", path.c_str() );
    unlink( temporary.c_str() );
    return false;
}

template <typename Entity>
static bool write_realm_list( const char *path, const Entity *first, const Entity *added = nullptr )
{
    return write_realm_file( path, [&]( FILE *fp ) {
        for ( const Entity *entity = first; entity; entity = entity->next )
        {
            if ( !entity->filename || !realm_filename_valid( entity->filename ) )
                return false;
            fprintf( fp, "%s\n", entity->filename );
        }
        if ( added )
            fprintf( fp, "%s\n", added->filename );
        fprintf( fp, "$\n" );
        return true;
    } );
}

template <typename Entity>
static bool realm_filename_available( CHAR_DATA *ch, const char *dir, const std::string &name,
                                      const Entity *first )
{
    if ( name.size() > 230 || !realm_filename_valid( name ) )
    {
        send_to_char( "Choose a valid filename using letters, numbers, dots, hyphens or underscores.\n", ch );
        return false;
    }
    for ( const Entity *entity = first; entity; entity = entity->next )
    {
        if ( entity->filename && !str_cmp( entity->filename, name ) )
        {
            send_to_char( "That filename is already in use.\n", ch );
            return false;
        }
    }
    if ( !is_valid_filename( ch, dir, name ) )
        return false;

    struct stat info;
    const std::string path = std::string( dir ) + name;
    if ( lstat( path.c_str(), &info ) < 0 && errno == ENOENT )
        return true;

    send_to_char( "That file cannot be used.\n", ch );
    return false;
}

static bool clan_filename_available( CHAR_DATA *ch, const std::string &name )
{
    if ( !realm_filename_available( ch, CLAN_DIR, name, first_clan ) )
        return false;

    struct stat info;
    const std::string path = std::string( CLAN_DIR ) + name;
    for ( const char *suffix : { ".mem", ".vault" } )
    {
        if ( lstat( (path + suffix).c_str(), &info ) == 0 || errno != ENOENT )
        {
            send_to_char( "That filename already has member or vault data.\n", ch );
            return false;
        }
    }
    return true;
}

static std::string realm_filename_for_name( const std::string &name, const char *extension )
{
    std::string filename;
    for ( const char letter : name )
    {
        if ( (letter >= 'A' && letter <= 'Z') || (letter >= 'a' && letter <= 'z')
        ||   (letter >= '0' && letter <= '9') )
            filename += static_cast<char>( LOWER( letter ) );
        else if ( !filename.empty() && filename.back() != '-' )
            filename += '-';
    }
    if ( filename.empty() )
        return {};
    if ( filename.back() == '-' )
        filename.pop_back();
    return filename + extension;
}

static bool copy_clan_vault( const std::string &old_path, const std::string &new_path )
{
    FILE *source = fopen( old_path.c_str(), "rb" );
    if ( !source )
        return errno == ENOENT;

    const bool saved = write_realm_file( new_path, [&]( FILE *target ) {
        char buffer[4096];
        size_t count;
        while ( (count = fread( buffer, 1, sizeof( buffer ), source )) != 0 )
            if ( fwrite( buffer, 1, count, target ) != count )
                return false;
        return !ferror( source );
    } );
    fclose( source );
    return saved;
}

/*
 * Save a clan's data to its data file
 */
static bool save_clan_file( const CLAN_DATA *clan )
{
    if ( !clan )
    {
	bug( "save_clan: null clan pointer!" );
	return false;
    }
        
    if ( !clan->filename || !realm_filename_valid( clan->filename ) )
    {
	bug( "save_clan: %s has no valid filename", clan->name ? clan->name : "(null)" );
	return false;
    }
 
    return write_realm_file( std::string( CLAN_DIR ) + clan->filename, [&]( FILE *fp ) {
	fprintf( fp, "#CLAN\n" );
	fprintf( fp, "Name         %s~\n",	clan->name ? clan->name : "" );
	fprintf( fp, "Filename     %s~\n",	clan->filename		);
	fprintf( fp, "Description  %s~\n",	clan->description ? clan->description : "" );
	fprintf( fp, "Leader       %s~\n",	clan->leader ? clan->leader : "" );
	fprintf( fp, "NumberOne    %s~\n",	clan->number1 ? clan->number1 : "" );
	fprintf( fp, "NumberTwo    %s~\n",	clan->number2 ? clan->number2 : "" );
	fprintf( fp, "PKills       %d\n",	clan->pkills		);
	fprintf( fp, "PDeaths      %d\n",	clan->pdeaths		);
	fprintf( fp, "MKills       %d\n",	clan->mkills		);
	fprintf( fp, "MDeaths      %d\n",	clan->mdeaths		);
	fprintf( fp, "Type         %d\n",	clan->clan_type		);
	fprintf( fp, "Members      %d\n",	clan->members		);
	fprintf( fp, "Board        %d\n",	clan->board		);
	fprintf( fp, "Storeroom    %d\n",	clan->storeroom		);
	fprintf( fp, "GuardOne     %d\n",	clan->guard1		);
	fprintf( fp, "GuardTwo     %d\n",	clan->guard2		);
	fprintf( fp, "PatrolOne    %d\n",	clan->patrol1		);
	fprintf( fp, "PatrolTwo    %d\n",	clan->patrol2		);
	fprintf( fp, "TrooperOne   %d\n",	clan->trooper1		);
	fprintf( fp, "TrooperTwo   %d\n",	clan->trooper2		);
	fprintf( fp, "Funds        %ld\n",	clan->funds		);
	fprintf( fp, "Enlist1      %d\n", clan->enlistroom1 );
	fprintf( fp, "Enlist2      %d\n", clan->enlistroom2 );
	fprintf( fp, "Jail         %d\n",       clan->jail            	);
	if ( clan->mainclan && clan->mainclan->name )
   	   fprintf( fp, "MainClan     %s~\n",	clan->mainclan->name	);
	fprintf( fp, "End\n\n"						);
	fprintf( fp, "#END\n"						);
        return true;
    } );
}

void save_clan( CLAN_DATA *clan )
{
    save_clan_file( clan );
}

static bool save_planet_file( const PLANET_DATA *planet )
{
    if ( !planet )
    {
	bug( "save_planet: null planet pointer!" );
	return false;
    }
        
    if ( !planet->filename || !realm_filename_valid( planet->filename ) )
    {
	bug( "save_planet: %s has no valid filename", planet->name ? planet->name : "(null)" );
	return false;
    }
 
    return write_realm_file( std::string( PLANET_DIR ) + planet->filename, [&]( FILE *fp ) {
        AREA_DATA *pArea;
        
	fprintf( fp, "#PLANET\n" );
	fprintf( fp, "Name         %s~\n",	planet->name ? planet->name : "" );
	fprintf( fp, "Filename     %s~\n",	planet->filename        );
	fprintf( fp, "BaseValue    %ld\n",	planet->base_value      );
	fprintf( fp, "Flags        %d\n",	planet->flags           );
	fprintf( fp, "PopSupport   %f\n",	planet->pop_support      );
	if ( planet->spaceobject && planet->spaceobject->name )
        	fprintf( fp, "spaceobject   %s~\n",	planet->spaceobject->name);
	if ( planet->governed_by && planet->governed_by->name )
        	fprintf( fp, "GovernedBy   %s~\n",	planet->governed_by->name);
	for( pArea = planet->first_area ; pArea ; pArea = pArea->next_on_planet )
	    if (pArea->filename)
         	fprintf( fp, "Area         %s~\n",	pArea->filename  );
	fprintf( fp, "End\n\n"						);
	fprintf( fp, "#END\n"						);
        return true;
    } );
}

void save_planet( PLANET_DATA *planet )
{
    save_planet_file( planet );
}


/*
 * Read in actual clan data.
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

static bool parse_clan_amount(const char *input, long &amount)
{
    if ( !input )
        return false;
    char *end;
    errno = 0;
    amount = strtol(input, &end, 10);
    if ( errno == ERANGE || end == input )
        return false;
    while ( isspace(static_cast<unsigned char>(*end)) )
        ++end;
    return *end == '\0';
}

static long read_clan_funds(FILE *fp)
{
    long funds;
    if ( parse_clan_amount(fread_word(fp), funds) )
        return funds;
    bug("Fread_clan: invalid funds balance");
    return 0;
}

void fread_clan( CLAN_DATA *clan, FILE *fp )
{
    const char *word;
    bool fMatch;

    for ( ; ; )
    {
	word   = feof( fp ) ? "End" : fread_word( fp );
	if ( !word || word[0] == '\0' )
	    word = "End";
	fMatch = FALSE;

	switch ( UPPER(word[0]) )
	{
	case '*':
	    fMatch = TRUE;
	    fread_to_eol( fp );
	    break;

	case 'B':
	    KEY( "Board",	clan->board,		fread_number( fp ) );
	    break;

	case 'D':
	    KEY( "Description",	clan->description,	fread_string( fp ) );
	    break;

	case 'E':
            KEY( "Enlist1",	clan->enlistroom1,		fread_number( fp ) );
            KEY( "Enlist2",	clan->enlistroom2,		fread_number( fp ) );
	    if ( !str_cmp( word, "End" ) )
	    {
		if (!clan->name)
		  clan->name		= STRALLOC( "" );
		if (!clan->leader)
		  clan->leader		= STRALLOC( "" );
		if (!clan->description)
		  clan->description 	= STRALLOC( "" );
		if (!clan->number1)
		  clan->number1		= STRALLOC( "" );
		if (!clan->number2)
		  clan->number2		= STRALLOC( "" );
		if (!clan->tmpstr)
		  clan->tmpstr		= STRALLOC( "" );
		return;
	    }
	    break;
	    
	case 'F':
	    KEY( "Funds",	clan->funds,		read_clan_funds(fp) );
	    KEY( "Filename",	clan->filename,		fread_string_nohash( fp ) );
	    break;

	case 'G':
	    KEY( "GuardOne",	clan->guard1,		fread_number( fp ) );
	    KEY( "GuardTwo",	clan->guard2,		fread_number( fp ) );
	    break;

	case 'J':
	    KEY( "Jail",	clan->jail,		fread_number( fp ) );
	    break;

	case 'L':
	    KEY( "Leader",	clan->leader,		fread_string( fp ) );
	    break;

	case 'M':
	    KEY( "MDeaths",	clan->mdeaths,		fread_number( fp ) );
	    KEY( "Members",	clan->members,		fread_number( fp ) );
	    KEY( "MKills",	clan->mkills,		fread_number( fp ) );
	    KEY( "MainClan",	clan->tmpstr,		fread_string( fp ) );
	    break;
 
	case 'N':
	    KEY( "Name",	clan->name,		fread_string( fp ) );
	    KEY( "NumberOne",	clan->number1,		fread_string( fp ) );
	    KEY( "NumberTwo",	clan->number2,		fread_string( fp ) );
	    break;

	case 'P':
	    KEY( "PDeaths",	clan->pdeaths,		fread_number( fp ) );
	    KEY( "PKills",	clan->pkills,		fread_number( fp ) );
	    KEY( "PatrolOne",	clan->patrol1,		fread_number( fp ) );
	    KEY( "PatrolTwo",	clan->patrol2,		fread_number( fp ) );
	    break;

	case 'S':
	    KEY( "Storeroom",	clan->storeroom,	fread_number( fp ) );
	    break;

	case 'T':
	    KEY( "Type",	clan->clan_type,	fread_number( fp ) );
	    KEY( "TrooperOne",	clan->trooper1,		fread_number( fp ) );
	    KEY( "TrooperTwo",	clan->trooper2,		fread_number( fp ) );
	    break;
	}
	
	if ( !fMatch )
	{
	    bug( "Fread_clan: no match: %s", word );
	}
	
    }
}

void fread_planet( PLANET_DATA *planet, FILE *fp )
{
    const char *word;
    bool fMatch;

    for ( ; ; )
    {
	word   = feof( fp ) ? "End" : fread_word( fp );
	if ( !word || word[0] == '\0' )
	    word = "End";
	fMatch = FALSE;

	switch ( UPPER(word[0]) )
	{
	case '*':
	    fMatch = TRUE;
	    fread_to_eol( fp );
	    break;

	case 'A':
	    if ( !str_cmp( word, "Area" ) )
	    {
	        char * tmp;
            std::string aName;
                AREA_DATA *pArea;
                	        
	     	tmp = fread_string(fp);
            aName = tmp;
            STRFREE( tmp );
		for( pArea = first_area ; pArea ; pArea = pArea->next )
	          if (pArea->filename && !str_cmp(pArea->filename , aName ) )
	          {
	             pArea->planet = planet; 
	             LINK( pArea, planet->first_area, planet->last_area, next_on_planet, prev_on_planet);
	          }      
                fMatch = TRUE;
	    }
	    break;

	case 'B':
	    KEY( "BaseValue",	planet->base_value,		fread_number( fp ) );
	    break;

	case 'E':
	    if ( !str_cmp( word, "End" ) )
	    {
		if (!planet->name)
		  planet->name		= STRALLOC( "" );
		return;
	    }
	    break;

	case 'F':
	    KEY( "Filename",	planet->filename,		fread_string_nohash( fp ) );
	    KEY( "Flags",	planet->flags,		        fread_number( fp ) );
	    break;
	
	case 'G':
	    if ( !str_cmp( word, "GovernedBy" ) )
	    {
            char *clan_name = fread_string( fp );
            planet->governed_by = get_clan( std::string(clan_name) );
            fMatch = TRUE;
            STRFREE( clan_name );
	    }
	    break;
	
	case 'N':
	    KEY( "Name",	planet->name,		fread_string( fp ) );
	    break;
	
	case 'P':
	    KEY( "PopSupport",	planet->pop_support,		fread_float( fp ) );
	    break;

	case 'S':
	    if ( !str_cmp( word, "spaceobject" ) )
	    {
            char *spaceobject_name = fread_string( fp );
	     	planet->spaceobject = spaceobject_from_name ( planet->game, std::string(spaceobject_name) );
            STRFREE( spaceobject_name );
            if (planet->spaceobject)
            {
                    SPACE_DATA *spaceobject = planet->spaceobject;
                    
                spaceobject->planet = planet;
            }
            fMatch = TRUE;
	    }
	    break;
	
	case 'T':
	    KEY( "Taxes",	planet->base_value,		fread_number( fp ) );
	    break;
    
	}
	
	if ( !fMatch )
	{
	    bug( "Fread_planet: no match: %s", word );
	}
	
    }
}


/*
 * Load a clan file
 */

bool load_clan_file( GameContext *game, const std::string& clanfile )
{
    std::string filename;
    CLAN_DATA *clan;
    FILE *fp;
    bool found;

    CREATE( clan, CLAN_DATA, 1 );
    clan->game = game;
    clan->next_subclan = NULL;
    clan->prev_subclan = NULL;
    clan->last_subclan = NULL;
    clan->first_subclan = NULL;    
    clan->mainclan     = NULL;
    
    found = FALSE;
    filename = str_printf("%s%s", CLAN_DIR, clanfile.c_str() );

    if ( ( fp = fopen( filename.c_str(), "r" ) ) != NULL )
    {
	for ( ; ; )
	{
	    char letter;
	    char *word;

	    if ( feof( fp ) )
		break;

	    letter = fread_letter( fp );
	    if ( letter == '\0' )
		break;

	    if ( letter == '*' )
	    {
		fread_to_eol( fp );
		continue;
	    }

	    if ( letter != '#' )
	    {
		bug( "Load_clan_file: # not found." );
		break;
	    }

	    word = fread_word( fp );
	    if ( !str_cmp( word, "CLAN"	) )
	    {
	    	fread_clan( clan, fp );
	    	found = TRUE;
	    	break;
	    }
	    else
	    if ( !str_cmp( word, "END"	) )
	        break;
	    else
	    {
		bug( "Load_clan_file: bad section: %s.", word );
		break;
	    }
	}
	FCLOSE( fp );
    }

    if ( found )
    {
	ROOM_INDEX_DATA *storeroom;

	LINK( clan, first_clan, last_clan, next, prev );

         if( !clan->filename || clan->filename[0] == '\0' || !load_member_list( game, clan->filename ) )
         {
             MEMBER_LIST *members_list;
             struct stat fst;
             std::string mem_file = str_printf("%s%s.mem", CLAN_DIR, clan->filename ? clan->filename : "");
             bool mem_exists = (clan->filename && clan->filename[0] != '\0' && stat(mem_file.c_str(), &fst) == 0);

             if ( mem_exists )
                 bug( "load_clan_file: %s.mem failed to load; preserving file on disk", clan->filename );
             else
                 log_string( "No memberlist found, creating new list" );
             CREATE( members_list, MEMBER_LIST, 1 );
             members_list->game = game;
             members_list->name = STRALLOC( clan->name ? clan->name : "" );
             LINK( members_list, first_member_list, last_member_list, next, prev );
             if ( !mem_exists )
                 save_member_list( members_list );
         }

	if ( clan->storeroom == 0
	|| (storeroom = get_room_index( clan->storeroom )) == NULL )
	{
	    log_string( "Storeroom not found" );
	    return found;
	}
	
	if ( !clan->filename || clan->filename[0] == '\0' )
	{
	    bug( "load_clan_file: %s has no filename; skipping vault load",
		 clan->name ? clan->name : "(null)" );
	    return found;
	}

	filename = str_printf("%s%s.vault", CLAN_DIR, clan->filename );
	if ( ( fp = fopen( filename.c_str(), "r" ) ) != NULL )
	{
//	    bool found;
	    OBJ_DATA *tobj, *tobj_next;

	    log_string( "Loading clan storage room" );
	    rset_supermob(storeroom);
	    ObjectNest nest{};

//	    found = TRUE;
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
		    bug( "Load_clan_vault: # not found." );
		    bug( "%s", clan->name ? clan->name : "(null)" );
		    break;
		}

		word = fread_word( fp );
		if ( !str_cmp( word, "OBJECT" ) )	/* Objects	*/
		  fread_obj(game, supermob, fp, OS_CARRY, nest);
		else
		if ( !str_cmp( word, "END"    ) )	/* Done		*/
		  break;
		else
		{
		    bug( "Load_clan_vault: bad section." );
		    bug( "%s", clan->name ? clan->name : "(null)" );
		    break;
		}
	    }
	    FCLOSE( fp );
	    for ( tobj = supermob->first_carrying; tobj; tobj = tobj_next )
	    {
		tobj_next = tobj->next_content;
		obj_from_char( tobj );
		obj_to_room( tobj, storeroom );
	    }
	    release_supermob(game);
	}
	else
	    log_string( "Cannot open clan vault" );
    }
    else
      DISPOSE( clan );

    return found;
}

bool load_planet_file( GameContext *game, const std::string& planetfile )
{
    std::string filename;
    PLANET_DATA *planet;
    FILE *fp;
    bool found;

    CREATE( planet, PLANET_DATA, 1 );
    planet->game = game;
    
    planet->governed_by = NULL;
    planet->next_in_system = NULL;
    planet->prev_in_system = NULL;
    planet->spaceobject = NULL ;
    planet->first_area = NULL;
    planet->last_area = NULL;
    planet->first_guard = NULL;
    planet->last_guard = NULL;
    
    found = FALSE;
    filename = str_printf("%s%s", PLANET_DIR, planetfile.c_str() );

    if ( ( fp = fopen( filename.c_str(), "r" ) ) != NULL )
    {
	for ( ; ; )
	{
	    char letter;
	    char *word;

	    if ( feof( fp ) )
		break;

	    letter = fread_letter( fp );
	    if ( letter == '\0' )
		break;

	    if ( letter == '*' )
	    {
		fread_to_eol( fp );
		continue;
	    }

	    if ( letter != '#' )
	    {
		bug( "Load_planet_file: # not found." );
		break;
	    }

	    word = fread_word( fp );
	    if ( !str_cmp( word, "PLANET"	) )
	    {
	    	fread_planet( planet, fp );
	    	found = TRUE;
	    	break;
	    }
	    else
	    if ( !str_cmp( word, "END"	) )
	        break;
	    else
	    {
		bug( "Load_planet_file: bad section: %s.", word );
		break;
	    }
	}
	FCLOSE( fp );
    }

    if ( !found )
      DISPOSE( planet );
    else
      LINK( planet, first_planet, last_planet, next, prev );

    return found;
}


/*
 * Load in all the clan files.
 */
void load_clans( GameContext *game )
{
    FILE *fpList;
    std::string filename;
    std::string clanlist;
    CLAN_DATA *clan;
    CLAN_DATA *bosclan;
    
    first_clan	= NULL;
    last_clan	= NULL;

    log_string( "Loading clans..." );

    clanlist = str_printf("%s%s", CLAN_DIR, CLAN_LIST );
    FCLOSE( fpReserve );
    if ( ( fpList = fopen( clanlist.c_str(), "r" ) ) == NULL )
    {
	perror( clanlist.c_str() );
	exit( 1 );
    }

    for ( ; ; )
    {
	filename = feof( fpList ) ? "$" : fread_word( fpList );
	log_string( filename.c_str() );
	if ( filename[0] == '$' )
	  break;

	if ( !load_clan_file( game, std::string( filename ) ) )
	{
	  bug( "Cannot load clan file: %s", filename.c_str() );
	}
    }
    FCLOSE( fpList );
    log_string(" Done clans\nSorting clans...." );
    fpReserve = fopen( NULL_FILE, "r" );
    
    for ( clan=first_clan ; clan ; clan = clan->next )
    {
       if ( !clan->tmpstr || clan->tmpstr[0] == '\0' )
         continue;
         
       bosclan = get_clan ( std::string(clan->tmpstr) );
       if ( !bosclan ) 
         continue;
         
       LINK( clan , bosclan->first_subclan , bosclan->last_subclan , next_subclan, prev_subclan );
       clan->mainclan = bosclan;
    }
    
    log_string(" Done sorting" );
    return;
}

void load_planets( GameContext *game )
{
    FILE *fpList;
    std::string filename;
    std::string planetlist;
    
    first_planet	= NULL;
    last_planet	= NULL;

    log_string( "Loading planets..." );

    planetlist = str_printf("%s%s", PLANET_DIR, PLANET_LIST );
    FCLOSE( fpReserve );
    if ( ( fpList = fopen( planetlist.c_str(), "r" ) ) == NULL )
    {
	perror( planetlist.c_str() );
	exit( 1 );
    }

    for ( ; ; )
    {
	filename = feof( fpList ) ? "$" : fread_word( fpList );
	log_string( filename.c_str() );
	if ( filename[0] == '$' )
	  break;

	if ( !load_planet_file( game, filename ) )
	{
	  bug( "Cannot load planet file: %s", filename.c_str() );
	}
    }
    FCLOSE( fpList );
    log_string(" Done planets " );  
    fpReserve = fopen( NULL_FILE, "r" );
    return;
}

void do_make( CHAR_DATA *ch, char *argument )
{
	send_to_char( "Huh?\n", ch );
	return;
}

void do_induct( CHAR_DATA *ch, char *argument )
{
    std::string arg;
    CHAR_DATA *victim;
    CLAN_DATA *clan;

    if ( IS_NPC( ch ) || !ch->pcdata || !ch->pcdata->clan )
    {
	send_to_char( "Huh?\n", ch );
	return;
    }

    clan = ch->pcdata->clan;
    
    if ( (ch->pcdata && ch->pcdata->bestowments
    &&    is_name("induct", ch->pcdata->bestowments))
    ||   (ch->name && clan->leader && !str_cmp( ch->name, clan->leader ))
    ||   (ch->name && clan->number1 && !str_cmp( ch->name, clan->number1 ))
    ||   (ch->name && clan->number2 && !str_cmp( ch->name, clan->number2 )) )
	;
    else
    {
	send_to_char( "Huh?\n", ch );
	return;
    }

    one_argument( argument ? argument : "", arg );

    if ( arg.empty() )
    {
	send_to_char( "Induct whom?\n", ch );
	return;
    }

    if ( ( victim = get_char_world( ch, arg ) ) == NULL )
    {
	send_to_char( "That player is not here.\n", ch);
	return;
    }

    if ( IS_NPC(victim) )
    {
	send_to_char( "Not on NPC's.\n", ch );
	return;
    }

    if ( victim->pcdata->clan )
    {
      if ( victim->pcdata->clan->clan_type == CLAN_CRIME )
      {
	if ( victim->pcdata->clan == clan )
	  send_to_char( "This player already belongs to your crime family!\n", ch );
	else
	  send_to_char( "This player already belongs to an organization!\n", ch );
	return;
      }
      else if ( victim->pcdata->clan->clan_type == CLAN_GUILD )
      {
	if ( victim->pcdata->clan == clan )
	  send_to_char( "This player already belongs to your guild!\n", ch );
	else
	  send_to_char( "This player already belongs to an organization!\n", ch );
	return;
      }
      else
      {
	if ( victim->pcdata->clan == clan )
	  send_to_char( "This player already belongs to your organization!\n", ch );
	else
	  send_to_char( "This player already belongs to an organization!\n", ch );
	return;
      }
      
    }
    
    if ( !clan->name || clan->name[0] == '\0' )
    {
        send_to_char( "That organization has no name and cannot induct members.\n", ch );
        return;
    }

    clan->members++;

    victim->pcdata->clan = clan;
    if ( victim->pcdata->clan_name )
        STRFREE(victim->pcdata->clan_name);
    victim->pcdata->clan_name = STRALLOC( clan->name );
    update_member( victim );
    act( AT_MAGIC, "You induct $N into $t", ch, clan->name, victim, TO_CHAR );
    act( AT_MAGIC, "$n inducts $N into $t", ch, clan->name, victim, TO_NOTVICT );
    act( AT_MAGIC, "$n inducts you into $t", ch, clan->name, victim, TO_VICT );
    save_char_obj( victim );
    return;
}

/* Can the character outcast the victim? - FUSS/DV 3-13-26 */
bool can_outcast( CLAN_DATA *clan, CHAR_DATA *ch, CHAR_DATA *victim )
{
   if( !clan || !ch || !victim || !ch->name || !victim->name )
      return FALSE;
   if( clan->leader && !str_cmp( ch->name, clan->leader ) )
      return TRUE;
   if( clan->leader && !str_cmp( victim->name, clan->leader ) )
      return FALSE;
   if( clan->number1 && !str_cmp( ch->name, clan->number1 ) )
      return TRUE;
   if( clan->number1 && !str_cmp( victim->name, clan->number1 ) )
      return FALSE;
   if( clan->number2 && !str_cmp( ch->name, clan->number2 ) )
      return TRUE;
   if( clan->number2 && !str_cmp( victim->name, clan->number2 ) )
      return FALSE;
   return TRUE;
}

void do_outcast( CHAR_DATA *ch, char *argument )
{
    std::string arg;
    CHAR_DATA *victim;
    CLAN_DATA *clan;

    if ( IS_NPC( ch ) || !ch->pcdata || !ch->pcdata->clan )
    {
	send_to_char( "Huh?\n", ch );
	return;
    }

    clan = ch->pcdata->clan;

    if ( (ch->pcdata && ch->pcdata->bestowments
    &&    is_name("outcast", ch->pcdata->bestowments))
    ||   (ch->name && clan->leader && !str_cmp( ch->name, clan->leader ))
    ||   (ch->name && clan->number1 && !str_cmp( ch->name, clan->number1 ))
    ||   (ch->name && clan->number2 && !str_cmp( ch->name, clan->number2 )) )
	;
    else
    {
	send_to_char( "Huh?\n", ch );
	return;
    }


    one_argument( argument ? argument : "", arg );

    if ( arg.empty() )
    {
	send_to_char( "Outcast whom?\n", ch );
	return;
    }

    if ( ( victim = get_char_world( ch, arg ) ) == NULL )
    {
	send_to_char( "That player is not here.\n", ch);
	return;
    }

    if ( IS_NPC(victim) )
    {
	send_to_char( "Not on NPC's.\n", ch );
	return;
    }

    if ( victim == ch )
    {
	    send_to_char( "Kick yourself out of your own clan?\n", ch );
	    return;
    }
 
    if ( victim->pcdata->clan != ch->pcdata->clan )
    {
	    send_to_char( "This player does not belong to your clan!\n", ch );
	    return;
    }
    if( !can_outcast( clan, ch, victim ) )
    {
        send_to_char( "You are not able to outcast them.\n", ch );
        return;
    }

    if ( victim->speaking == LANG_CLAN )
        victim->speaking = LANG_COMMON;
    BV_REMOVE_BIT( victim->speaks, LANG_CLAN );
    --clan->members;
    if( clan->members < 0 ) //--Shaddai/FUSS - DV added 3-13-26
        clan->members = 0;    
    if ( victim->name && ch->pcdata->clan->number1 && !str_cmp( victim->name, ch->pcdata->clan->number1 ) )
    {
	STRFREE( ch->pcdata->clan->number1 );
	ch->pcdata->clan->number1 = STRALLOC( "" );
    }
    if ( victim->name && ch->pcdata->clan->number2 && !str_cmp( victim->name, ch->pcdata->clan->number2 ) )
    {
	STRFREE( ch->pcdata->clan->number2 );
	ch->pcdata->clan->number2 = STRALLOC( "" );
    }
    victim->pcdata->clan = NULL;
    remove_member( victim );
    if ( victim->pcdata->clan_name )
        STRFREE(victim->pcdata->clan_name);
    victim->pcdata->clan_name = STRALLOC( "" );
    act( AT_MAGIC, "You outcast $N from $t", ch, clan->name ? clan->name : "(unnamed)", victim, TO_CHAR );
    act( AT_MAGIC, "$n outcasts $N from $t", ch, clan->name ? clan->name : "(unnamed)", victim, TO_ROOM );
    act( AT_MAGIC, "$n outcasts you from $t", ch, clan->name ? clan->name : "(unnamed)", victim, TO_VICT );
    
    if ( victim->pcdata->bestowments )
        STR_DISPOSE( victim->pcdata->bestowments );
    victim->pcdata->bestowments = str_dup("");
    
    save_clan(clan);
    save_char_obj(victim);
    return;
}

void do_setclan( CHAR_DATA *ch, char *argument )
{
    std::string arg1;
    std::string arg2;
    std::string argstr = argument ? argument : "";
    CLAN_DATA *clan;

    if ( IS_NPC( ch ) )
    {
	send_to_char( "Huh?\n", ch );
	return;
    }

    argstr = one_argument( argstr, arg1 );
    argstr = one_argument( argstr, arg2 );

    if ( arg1.empty() )
    {
	send_to_char( "Usage: setclan <clan> <field> <leader|number1|number2> <player>\n", ch );
	send_to_char( "\nField being one of:\n", ch );
	send_to_char( " leader number1 number2 subclan enlistroom1\n", ch );
	send_to_char( " enlistroom2 members board type storage\n", ch );
	send_to_char( " funds trooper1 trooper2 jail", ch );	
	send_to_char( " guard1 guard2 patrol1 patrol2\n", ch );
	if ( get_trust( ch ) >= LEVEL_SUB_IMPLEM )
	{
	  send_to_char( " name filename desc\n", ch );
	}
	return;
    }

    clan = get_clan( arg1 );
    if ( !clan )
    {
	send_to_char( "No such clan.\n", ch );
	return;
    }

	if ( !str_cmp( arg2, "enlistroom1" ) )
	{
		clan->enlistroom1 = strtoi( argstr );
		send_to_char( "Done.\n", ch );
		save_clan( clan );
		return;
	}
	if ( !str_cmp( arg2, "enlistroom2" ) )
	{
		clan->enlistroom2 = strtoi( argstr );
		send_to_char( "Done.\n", ch );
		save_clan( clan );
		return;
	}
    if ( !str_cmp( arg2, "leader" ) )
    {
	if ( clan->leader )
	    STRFREE( clan->leader );
	clan->leader = STRALLOC( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    if ( !str_cmp( arg2, "subclan" ) )
    {
        CLAN_DATA *subclan;
        subclan = get_clan( argstr );
        if ( !subclan )
        {
            send_to_char( "Subclan is not a clan.\n", ch );
            return;
        }
        if ( subclan->clan_type == CLAN_SUBCLAN || subclan->mainclan )
        {
            send_to_char( "Subclan is already part of another organization.\n", ch );
            return;
        }
        if ( subclan->first_subclan )
        {
            send_to_char( "Subclan has subclans of its own that need removing first.\n", ch );
            return;
        }
        subclan->clan_type = CLAN_SUBCLAN;
        subclan->mainclan = clan;
        LINK(subclan, clan->first_subclan, clan->last_subclan, next_subclan, prev_subclan );
	save_clan( clan );
	save_clan( subclan );
	return;
    }

    if ( !str_cmp( arg2, "number1" ) )
    {
	if ( clan->number1 )
	    STRFREE( clan->number1 );
	clan->number1 = STRALLOC( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    if ( !str_cmp( arg2, "number2" ) )
    {
	if ( clan->number2 )
	    STRFREE( clan->number2 );
	clan->number2 = STRALLOC( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    if ( !str_cmp( arg2, "board" ) )
    {
	clan->board = strtoi( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    if ( !str_cmp( arg2, "members" ) )
    {
	clan->members = strtoi( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    if ( !str_cmp( arg2, "funds" ) )
    {
	clan->funds = strtoi( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    if ( !str_cmp( arg2, "storage" ) )
    {
	clan->storeroom = strtoi( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    if ( !str_cmp( arg2, "guard1" ) )
    {
	clan->guard1 = strtoi( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    if ( !str_cmp( arg2, "jail" ) )
    {
	clan->jail = strtoi( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    if ( !str_cmp( arg2, "guard2" ) )
    {
	clan->guard2 = strtoi( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    if ( !str_cmp( arg2, "trooper1" ) )
    {
	clan->trooper1 = strtoi( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    if ( !str_cmp( arg2, "trooper2" ) )
    {
	clan->trooper2 = strtoi( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }
    if ( !str_cmp( arg2, "patrol1" ) )
    {
	clan->patrol1 = strtoi( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    if ( !str_cmp( arg2, "patrol2" ) )
    {
	clan->patrol2 = strtoi( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    if ( get_trust( ch ) < LEVEL_SUB_IMPLEM )
    {
	do_setclan( ch, "" );
	return;
    }
    
    if ( !str_cmp( arg2, "type" ) )
    {
        if ( clan->mainclan )
        {
            UNLINK ( clan , clan->mainclan->first_subclan, clan->mainclan->last_subclan, next_subclan, prev_subclan );
            clan->mainclan = NULL;
	}
	if ( !str_cmp( argstr, "crime" ) )
	  clan->clan_type = CLAN_CRIME;
	else
	if ( !str_cmp( argstr, "crime family" ) )
	  clan->clan_type = CLAN_CRIME;
	else
	if ( !str_cmp( argstr, "guild" ) )
	  clan->clan_type = CLAN_GUILD;
	else
	  clan->clan_type = 0;
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

   if( !str_cmp( arg2, "name" ) )
   {
      CLAN_DATA *uclan = NULL;

      if( argstr.empty() )
      {
         send_to_char( "You can't name a clan nothing.\n", ch );
         return;
      }
      if( ( uclan = get_clan( argstr ) ) )
      {
         send_to_char( "There is already another clan with that name.\n", ch );
         return;
      }
      if ( clan->name )
          STRFREE( clan->name );
      clan->name = STRALLOC( argstr );
      send_to_char( "Done.\n", ch );
      save_clan( clan );
      return;
   }

   if( !str_cmp( arg2, "filename" ) )
   {
      if ( clan->filename && argstr == clan->filename )
      {
         send_to_char( "Done.\n", ch );
         return;
      }
      if ( !clan_filename_available( ch, argstr ) )
         return;

      MEMBER_LIST *members = first_member_list;
      while ( members && (!members->name || str_cmp( members->name, clan->name )) )
         members = members->next;
      if ( !members )
      {
         send_to_char( "This clan has no member list.\n", ch );
         return;
      }

      char *old_filename = clan->filename;
      const std::string old_path = old_filename ? std::string( CLAN_DIR ) + old_filename : "";
      const std::string new_path = std::string( CLAN_DIR ) + argstr;
      clan->filename = str_dup( argstr );
      const bool saved = save_clan_file( clan ) && save_member_list_file( clan, members )
          && (old_path.empty() || copy_clan_vault( old_path + ".vault", new_path + ".vault" ))
          && write_realm_list( CLAN_DIR CLAN_LIST, first_clan );
      if ( !saved )
      {
         unlink( new_path.c_str() );
         unlink( (new_path + ".mem").c_str() );
         unlink( (new_path + ".vault").c_str() );
         STR_DISPOSE( clan->filename );
         clan->filename = old_filename;
         send_to_char( "Could not save the new clan filename.\n", ch );
         return;
      }

      if ( old_filename )
      {
         if ( realm_filename_valid( old_filename ) )
         {
            unlink( old_path.c_str() );
            unlink( (old_path + ".mem").c_str() );
            unlink( (old_path + ".vault").c_str() );
         }
         STR_DISPOSE( old_filename );
      }
      send_to_char( "Done.\n", ch );
      return;
   }

    if ( !str_cmp( arg2, "desc" ) )
    {
	if ( clan->description )
	    STRFREE( clan->description );
	clan->description = STRALLOC( argstr );
	send_to_char( "Done.\n", ch );
	save_clan( clan );
	return;
    }

    do_setclan( ch, "" );
    return;
}

void do_setplanet( CHAR_DATA *ch, char *argument )
{
    std::string arg1;
    std::string arg2;
    std::string argstr = argument ? argument : "";
    PLANET_DATA *planet;

    if ( IS_NPC( ch ) )
    {
	send_to_char( "Huh?\n", ch );
	return;
    }

    argstr = one_argument( argstr, arg1 );
    argstr = one_argument( argstr, arg2 );

    if ( arg1.empty() )
    {
	send_to_char( "Usage: setplanet <planet> <field> [value]\n", ch );
	send_to_char( "\nField being one of:\n", ch );
	send_to_char( " base_value flags\n", ch ); 
	send_to_char( " name filename spaceobject governed_by\n", ch );
	return;
    }

    planet = get_planet( arg1 );
    if ( !planet )
    {
	send_to_char( "No such planet.\n", ch );
	return;
    }


    if( !str_cmp( arg2, "name" ) )
    {
        PLANET_DATA *tplanet;
        if( argstr.empty() )
        {
            send_to_char( "You must choose a name.\n", ch );
            return;
        }
        if( ( tplanet = get_planet( argstr ) ) != NULL )
        {
            send_to_char( "A planet with that name already Exists!\n", ch );
            return;
        }

        if ( planet->name )
            STRFREE( planet->name );
        planet->name = STRALLOC( argstr );
        send_to_char( "Done.\n", ch );
        save_planet( planet );
        return;
    }

    if ( !str_cmp( arg2, "governed_by" ) )
    {
        CLAN_DATA *clan;
        clan = get_clan( argstr );
        if ( clan )
        { 
           planet->governed_by = clan;
           send_to_char( "Done.\n", ch ); 
       	   save_planet( planet );
        }
        else
           send_to_char( "No such clan.\n", ch ); 
	return;
    }

    if ( !str_cmp( arg2, "spaceobject" ) )
    {
        SPACE_DATA *spaceobject = argstr.empty() ? nullptr : spaceobject_from_name( planet->game, argstr );
        if ( !spaceobject )
        {
            send_to_char( "No such spaceobject.\n", ch );
            return;
        }
        if ( spaceobject->planet && spaceobject->planet != planet )
        {
            send_to_char( "That spaceobject already has a planet.\n", ch );
            return;
        }

        SPACE_DATA *previous = planet->spaceobject;
        planet->spaceobject = spaceobject;
        if ( !save_planet_file( planet ) )
        {
            planet->spaceobject = previous;
            send_to_char( "Could not save the planet's spaceobject.\n", ch );
            return;
        }
        if ( previous && previous != spaceobject && previous->planet == planet )
            previous->planet = nullptr;
        spaceobject->planet = planet;
        send_to_char( "Done.\n", ch );
        return;
    }

    if( !str_cmp( arg2, "filename" ) )
    {
        if ( planet->filename && argstr == planet->filename )
        {
            send_to_char( "Done.\n", ch );
            return;
        }
        if ( !realm_filename_available( ch, PLANET_DIR, argstr, first_planet ) )
            return;

        char *old_filename = planet->filename;
        planet->filename = str_dup( argstr );
        if ( !save_planet_file( planet ) || !write_realm_list( PLANET_DIR PLANET_LIST, first_planet ) )
        {
            unlink( (std::string( PLANET_DIR ) + argstr).c_str() );
            STR_DISPOSE( planet->filename );
            planet->filename = old_filename;
            send_to_char( "Could not save the new planet filename.\n", ch );
            return;
        }
        if ( old_filename )
        {
            if ( realm_filename_valid( old_filename ) )
                unlink( (std::string( PLANET_DIR ) + old_filename).c_str() );
            STR_DISPOSE( old_filename );
        }
        send_to_char( "Done.\n", ch );
        return;
    }

    if ( !str_cmp( arg2, "base_value" ) )
    {
	planet->base_value = strtoi( argstr );
	send_to_char( "Done.\n", ch );
	save_planet( planet );
	return;
    }

    if ( !str_cmp( arg2, "flags" ) )
    {
        std::string farg;
        
        argstr = one_argument( argstr, farg); 
        
        if ( farg.empty() )
        {
           send_to_char( "Possible flags: nocapture\n", ch );
           return;
        }
        
        for ( ; !farg.empty() ; argstr = one_argument( argstr, farg) )
        {
            if ( !str_cmp( farg, "nocapture" ) )
               TOGGLE_BIT( planet->flags, PLANET_NOCAPTURE );
            else
               ch_printf( ch , "No such flag: %s\n" , farg );
	}
	send_to_char( "Done.\n", ch );
	save_planet( planet );
	return;
    }

    do_setplanet( ch, "" );
    return;
}

void do_showclan( CHAR_DATA *ch, char *argument )
{   
    CLAN_DATA *clan;

    if ( IS_NPC( ch ) )
    {
	send_to_char( "Huh?\n", ch );
	return;
    }

    if ( !argument || argument[0] == '\0' )
    {
	send_to_char( "Usage: showclan <clan>\n", ch );
	return;
    }

    clan = get_clan( argument );
    if ( !clan )
    {
	send_to_char( "No such clan.\n", ch );
	return;
    }

    ch_printf( ch, "%s      : %s\nFilename: %s\n",
			clan->clan_type == CLAN_CRIME ? "Crime Family " : 
			    clan->clan_type == CLAN_GUILD ? "Guild " : "Organization ",
    			clan->name ? clan->name : "(none)",
    			clan->filename ? clan->filename : "(none)");
    ch_printf( ch, "Description: %s\nLeader: %s\n",
    			clan->description ? clan->description : "(none)",
    			clan->leader ? clan->leader : "(none)" );
    ch_printf( ch, "Number1: %s\nNumber2: %s\nPKills: %6d    PDeaths: %6d\n",
    			clan->number1 ? clan->number1 : "(none)",
    			clan->number2 ? clan->number2 : "(none)",
    			clan->pkills,
    			clan->pdeaths );
    ch_printf( ch, "MKills: %6d    MDeaths: %6d\n",
    			clan->mkills,
    			clan->mdeaths );
    ch_printf( ch, "Type: %d\n",
    			clan->clan_type );
    ch_printf( ch, "Members: %3d\n",
    			clan->members );
    ch_printf( ch, "Board: %5d   Jail: %5d\n",
    			clan->board, clan->jail);
    ch_printf( ch, "Guard1: %5d  Guard2: %5d\n",
    			clan->guard1,
    			clan->guard2 );
    ch_printf( ch, "Patrol1: %5d  Patrol2: %5d\n",
    			clan->patrol1,
    			clan->patrol2 );
    ch_printf( ch, "Trooper1: %5d  Trooper2: %5d\n",
    			clan->trooper1,
    			clan->trooper2 );
    ch_printf( ch, "Funds: %ld\n",
    			clan->funds );
    ch_printf( ch, "Enlist Room 1: %5d  Enlist Room 2: %5d\n",
    			clan->enlistroom1, clan->enlistroom2 );
    return;
}

void do_showplanet( CHAR_DATA *ch, char *argument )
{   
    PLANET_DATA *planet;

    if ( IS_NPC( ch ) )
    {
	send_to_char( "Huh?\n", ch );
	return;
    }

    if ( !argument || argument[0] == '\0' )
    {
	send_to_char( "Usage: showplanet <planet>\n", ch );
	return;
    }

    planet = get_planet( argument );
    if ( !planet )
    {
	send_to_char( "No such planet.\n", ch );
	return;
    }

    ch_printf( ch, "%s\nFilename: %s\nStarsystem: %s\n",
    			planet->name ? planet->name : "(none)",
    			planet->filename ? planet->filename : "(none)",
    			(planet->spaceobject && planet->spaceobject->name) ? planet->spaceobject->name : "None");
    return;
}

void do_makeclan( CHAR_DATA *ch, char *argument )
{
    const std::string name = argument ? argument : "";
    if ( name.empty() || name.find_first_of( "~\r\n" ) != std::string::npos )
    {
        send_to_char( "Usage: makeclan <clan name>\n", ch );
        return;
    }
    if ( get_clan( name ) )
    {
        send_to_char( "A clan with that name already exists.\n", ch );
        return;
    }

    const std::string filename = realm_filename_for_name( name, ".clan" );
    if ( !clan_filename_available( ch, filename ) )
        return;

    CLAN_DATA *clan;
    CREATE( clan, CLAN_DATA, 1 );
    clan->game = ch->game;
    clan->filename = str_dup( filename );
    clan->name = STRALLOC( name );
    clan->description = STRALLOC( "" );
    clan->leader = STRALLOC( "" );
    clan->number1 = STRALLOC( "" );
    clan->number2 = STRALLOC( "" );
    clan->tmpstr = STRALLOC( "" );

    MEMBER_LIST *members;
    CREATE( members, MEMBER_LIST, 1 );
    members->game = ch->game;
    members->name = QUICKLINK( clan->name );

    if ( !save_clan_file( clan ) || !save_member_list_file( clan, members )
    ||   !write_realm_list( CLAN_DIR CLAN_LIST, first_clan, clan ) )
    {
        const std::string path = std::string( CLAN_DIR ) + filename;
        unlink( path.c_str() );
        unlink( (path + ".mem").c_str() );
        STRFREE( members->name );
        DISPOSE( members );
        STRFREE( clan->name );
        STRFREE( clan->description );
        STRFREE( clan->leader );
        STRFREE( clan->number1 );
        STRFREE( clan->number2 );
        STRFREE( clan->tmpstr );
        STR_DISPOSE( clan->filename );
        DISPOSE( clan );
        send_to_char( "Could not save the new clan.\n", ch );
        return;
    }
    LINK( clan, first_clan, last_clan, next, prev );
    LINK( members, first_member_list, last_member_list, next, prev );
    send_to_char( "Clan created.\n", ch );
}

void do_makeplanet( CHAR_DATA *ch, char *argument )
{
    const std::string name = argument ? argument : "";
    if ( name.empty() || name.find_first_of( "~\r\n" ) != std::string::npos )
    {
        send_to_char( "Usage: makeplanet <planet name>\n", ch );
        return;
    }
    if ( get_planet( name ) )
    {
        send_to_char( "A planet with that name already exists.\n", ch );
        return;
    }

    const std::string filename = realm_filename_for_name( name, ".planet" );
    if ( !realm_filename_available( ch, PLANET_DIR, filename, first_planet ) )
        return;

    PLANET_DATA *planet;
    CREATE( planet, PLANET_DATA, 1 );
    planet->game = ch->game;
    planet->filename = str_dup( filename );
    planet->name = STRALLOC( name );

    if ( !save_planet_file( planet ) || !write_realm_list( PLANET_DIR PLANET_LIST, first_planet, planet ) )
    {
        unlink( (std::string( PLANET_DIR ) + filename).c_str() );
        STRFREE( planet->name );
        STR_DISPOSE( planet->filename );
        DISPOSE( planet );
        send_to_char( "Could not save the new planet.\n", ch );
        return;
    }
    LINK( planet, first_planet, last_planet, next, prev );
    send_to_char( "Planet created.\n", ch );
}

void do_clans( CHAR_DATA *ch, char *argument )
{
    CLAN_DATA *clan;
    PLANET_DATA *planet;
    int count = 0;
    int pCount = 0;
    float support;
    float revenue;
        
    for ( clan = first_clan; clan; clan = clan->next )
    {
        if ( clan->clan_type == CLAN_CRIME || clan->clan_type == CLAN_GUILD || clan->clan_type == CLAN_SUBCLAN )
          continue;
        
        pCount = 0;
        support = 0;
        revenue = 0;
        
        for ( planet = first_planet ; planet ; planet = planet->next )
          if ( clan == planet->governed_by )
          {
            support += planet->pop_support;
            pCount++;
            revenue += get_taxes(planet);
          }
          
        if ( pCount > 1 )
           support /= pCount;
	ch_printf( ch, "--------------------------------------------------------------------------------\n");
        ch_printf( ch, "&z&WOrganization: &Y%-20s    ", clan->name ? clan->name : "(none)");
	ch_printf( ch, "&WPlanets: &O%-4d &z&WAvg Pop Support: ",pCount);
	if (support <50)
	{
		ch_printf( ch, "&R");
	}
	else
	{
		ch_printf( ch, "&O");
	}
	ch_printf( ch,"%-3.1f&W\nRevenue: &O%-29.0f",support,revenue);
	ch_printf(ch,"&z&WLeader : ");
	if( clan->leader && clan->leader[0] != 0 )
	{
		ch_printf(ch,"&O%-20s",clan->leader);
	}
	else
	{
		ch_printf(ch,"&RNONE.             ");
	}
	ch_printf(ch, "&W\n");
        if ( clan->first_subclan )
        {
           CLAN_DATA *subclan;
           ch_printf( ch, "  &z&wSubclans             Leader\n");
           
           for ( subclan = clan->first_subclan ; subclan ; subclan = subclan->next_subclan ) 
           {
               ch_printf( ch, "  &O%-20s %-10s\n",
                  subclan->name ? subclan->name : "(none)",
                  subclan->leader ? subclan->leader : "" );
           }
        }
        count++;
    }
    ch_printf( ch, "--------------------------------------------------------------------------------\n");
    ch_printf( ch, "&z&WAutonomous Groups        Leader\n");
    for ( clan = first_clan; clan; clan = clan->next )
    {
        if ( clan->clan_type != CLAN_CRIME && clan->clan_type != CLAN_GUILD )
          continue;
        
        ch_printf( ch, "&Y%-24s &O%-10s\n",
                  clan->name ? clan->name : "(none)",
                  clan->leader ? clan->leader : "" );
        count++;
    }

    if ( !count )
    {
	set_char_color( AT_BLOOD, ch);
        send_to_char( "There are no organizations currently formed.\n", ch );
    }
    set_char_color( AT_WHITE, ch );
    
}

void do_planets( CHAR_DATA *ch, char *argument )
{
    PLANET_DATA *planet;
    int count = 0;
    AREA_DATA   *area;

    set_pager_color( AT_WHITE, ch );
    for ( planet = first_planet; planet; planet = planet->next )
    {
        pager_printf( ch, "&wPlanet: &G%-15s   &wGoverned By: &G%s %s\n", 
                   planet->name ? planet->name : "(none)",
                   (planet->governed_by && planet->governed_by->name) ? planet->governed_by->name : "",
                   IS_SET(planet->flags, PLANET_NOCAPTURE ) ? "(permanent)" : "" );
        pager_printf( ch, "&WValue: &O%-10.0f&W/&O%-10ld   ", 
                   get_taxes(planet) , planet->base_value);
        pager_printf( ch, "&WPopulation: &O%-5d   &W Pop Support: &R%.1f\n", 
                   planet->population , planet->pop_support );
        if ( IS_IMMORTAL(ch) )
        {
          pager_printf( ch, "&WAreas: &G");
          for ( area = planet->first_area ; area ; area = area->next_on_planet )
             pager_printf( ch , "%s,  ", area->filename ? area->filename : "(none)" );
          pager_printf( ch, "\n" );
        }         
//        pager_printf( ch, "\n" );
        
        count++;
    }

    if ( !count )
    {
	set_char_color( AT_BLOOD, ch);
        send_to_char( "There are no planets currently formed.\n", ch );
    }
    
}

void do_shove( CHAR_DATA *ch, char *argument )
{
    std::string arg;
    std::string arg2;
    std::string argstr = argument ? argument : "";
    int exit_dir;
    EXIT_DATA *pexit;
    CHAR_DATA *victim;
    bool nogo;
    ROOM_INDEX_DATA *to_room;
    ROOM_INDEX_DATA *fromroom;
    SHIP_DATA *ship;    
    int chance;  

    if ( !ch || !ch->in_room )
        return;

    argstr = one_argument( argstr, arg );
    argstr = one_argument( argstr, arg2 );

    
    if ( arg.empty() )
    {
	send_to_char( "Shove whom?\n", ch);
	return;
    }

    if ( ( victim = get_char_room( ch, arg ) ) == NULL )
    {
	send_to_char( "They aren't here.\n", ch);
	return;
    }

    if (victim == ch)
    {
	send_to_char("You shove yourself around, to no avail.\n", ch);
	return;
    }
    
    if ( IS_NPC(victim) )
    {
	send_to_char("You can only shove player characters.\n", ch);
	return;
    }

    if ( (victim->position) != POS_STANDING )
    {
	act( AT_PLAIN, "$N isn't standing up.", ch, NULL, victim, TO_CHAR );
	return;
    }

    if ( arg2.empty() )
    {
	send_to_char( "Shove them in which direction?\n", ch);
	return;
    }

    const bool entering_ship = !str_cmp(arg2, "in");
    const bool leaving_ship = !str_cmp(arg2, "out");
    exit_dir = entering_ship || leaving_ship ? -1 : get_dir(arg2);
    if ( BV_IS_SET(victim->in_room->room_flags, ROOM_SAFE)
    &&  get_timer(victim, TIMER_SHOVEDRAG) <= 0)
    {
	send_to_char("That character cannot be shoved right now.\n", ch);
	return;
    }
    nogo = FALSE;
    pexit = exit_dir < 0 ? nullptr : get_exit(ch->in_room, exit_dir);
    if ( !pexit )
    {
      if ( entering_ship )
      {
        if ( argstr.empty() )
        {
          send_to_char( "Drag him into what?\n", ch );
          return;
        }

        if ( ( ship = ship_in_room( ch->in_room , argstr ) ) == NULL )
        {
          act( AT_PLAIN, "I see no $T here.", ch, NULL, argstr, TO_CHAR );
          return;
        }
        
	if ( ch->mount )
   	{
          act( AT_PLAIN, "You can't go in there riding THAT.", ch, NULL, argstr, TO_CHAR );
          return;
   	} 

   	fromroom = ch->in_room;

        if ( ( to_room = get_room_index( ship->entrance ) ) != NULL )
   	{
   	   if ( ! ship->hatchopen )
   	   {
   	      send_to_char( "&RThe hatch is closed!\n", ch);
   	      return;
   	   }

           if ( to_room->tunnel > 0 )
           {
	        CHAR_DATA *ctmp;
	        int count = 0;

	       for ( ctmp = to_room->first_person; ctmp; ctmp = ctmp->next_in_room )
                   ++count;
	       if ( count + 2 > to_room->tunnel )
	       {
                  send_to_char( "There is no room for you both in there.\n", ch );
		  return;
	       }
           }
            if ( ship->shipstate == SHIP_LAUNCH || ship->shipstate == SHIP_LAUNCH_2 )
            {
                 send_to_char("&rThat ship has already started launching!\n",ch);
                 return;
            }
            
            act( AT_PLAIN, "$n enters $T.", ch,
		NULL, ship->name ? ship->name : "the ship" , TO_ROOM );
	    act( AT_PLAIN, "You enter $T.", ch,
		NULL, ship->name ? ship->name : "the ship" , TO_CHAR );
   	    char_from_room( ch );
   	    char_to_room( ch , to_room );
   	    act( AT_PLAIN, "$n enters the ship.", ch,
		NULL, argstr , TO_ROOM );
            act( AT_PLAIN, "$n enters $T.", victim,
		NULL, ship->name ? ship->name : "the ship" , TO_ROOM );
	    act( AT_PLAIN, "You enter $T.", victim,
		NULL, ship->name ? ship->name : "the ship" , TO_CHAR );
   	    char_from_room( victim );
   	    char_to_room( victim , to_room );
   	    act( AT_PLAIN, "$n enters the ship.", victim,
		NULL, argstr , TO_ROOM );
            do_look(ch, "auto");
            if ( !char_died(victim) && victim->in_room == to_room )
                do_look(victim, "auto");
	    return;
        }
        else
        {
          send_to_char("That ship has no entrance!\n", ch);
          return;
        }
      }  
      if ( leaving_ship )
      {
    	fromroom = ch->in_room;
    
	    if  ( (ship = ship_from_entrance(ch->game, fromroom->vnum)) == NULL )
	    {
	        send_to_char( "I see no exit here.\n" , ch );
	        return;
	    }   
        
	if ( ch->mount )
   	{
          act( AT_PLAIN, "You can't go out there riding THAT.", ch, NULL, argstr, TO_CHAR );
          return;
   	} 

    if ( ship->lastdoc != ship->location )
    {
        send_to_char("&rMaybe you should wait until the ship lands.\n",ch);
        return;
    }
    
    if ( ship->shipstate != SHIP_LANDED && ship->shipstate != SHIP_DISABLED )
    {
        send_to_char("&rPlease wait till the ship is properly docked.\n",ch);
        return;
    }
    
    if ( ! ship->hatchopen )
    {
    	send_to_char("&RYou need to open the hatch first" , ch );
    	return;
    }
    
    if ( ( to_room = get_room_index( ship->location ) ) != NULL )
    {

           if ( to_room->tunnel > 0 )
           {
	        CHAR_DATA *ctmp;
	        int count = 0;

	       for ( ctmp = to_room->first_person; ctmp; ctmp = ctmp->next_in_room )
                   ++count;
	       if ( count + 2 > to_room->tunnel )
	       {
                  send_to_char( "There is no room for you both in there.\n", ch );
		  return;
	       }
           }
            if ( ship->shipstate == SHIP_LAUNCH || ship->shipstate == SHIP_LAUNCH_2 )
            {
                 send_to_char("&rThat ship has already started launching!\n",ch);
                 return;
            }
            
            act( AT_PLAIN, "$n exits the ship.", ch,
		NULL, ship->name ? ship->name : "the ship" , TO_ROOM );
	    act( AT_PLAIN, "You exits the ship.", ch,
		NULL, ship->name ? ship->name : "the ship" , TO_CHAR );
   	    char_from_room( ch );
   	    char_to_room( ch , to_room );
   	    act( AT_PLAIN, "$n exits $T.", ch,
		NULL, ship->name ? ship->name : "the ship" , TO_ROOM );
            act( AT_PLAIN, "$n exits the ship.", victim,
		NULL, ship->name ? ship->name : "the ship" , TO_ROOM );
	    act( AT_PLAIN, "You exits the ship.", victim,
		NULL, ship->name ? ship->name : "the ship" , TO_CHAR );
   	    char_from_room( victim );
   	    char_to_room( victim , to_room );
   	    act( AT_PLAIN, "$n exits $T.", victim,
		NULL, ship->name ? ship->name : "the ship" , TO_ROOM );
            do_look(ch, "auto");
            if ( !char_died(victim) && victim->in_room == to_room )
                do_look(victim, "auto");
	    return;
        }
        else
        {
          send_to_char("That ship has no entrance!\n", ch);
          return;
        }
      }  
      nogo = TRUE;
    }
    else
    if ( IS_SET(pexit->exit_info, EX_CLOSED)
    && (!IS_AFFECTED(victim, AFF_PASS_DOOR)
    ||   IS_SET(pexit->exit_info, EX_NOPASSDOOR)) )
      nogo = TRUE;
    if ( nogo )
    {
	send_to_char( "There's no exit in that direction.\n", ch );
	return;
    }
    to_room = pexit->to_room;
    if ( !to_room )
    {
        send_to_char("There's no exit in that direction.\n", ch);
        return;
    }


    if (ch->in_room->area != to_room->area
    &&  !in_hard_range( victim, to_room->area ) )
    {
      send_to_char("That character cannot enter that area.\n", ch);
      return;
    }

chance = 50;

/* Add 3 points to chance for every str point above 15, subtract for 
below 15 */

chance += ((get_curr_str(ch) - 15) * 3);

chance += (ch->top_level - victim->top_level);
 
if (chance < number_percent( ))
{
  send_to_char("You failed.\n", ch);
  return;
}
    act( AT_ACTION, "You shove $M.", ch, NULL, victim, TO_CHAR );
    act( AT_ACTION, "$n shoves you.", ch, NULL, victim, TO_VICT );
    victim->position = POS_SHOVE;
    move_char(victim, pexit, 0);
    if ( !char_died(victim) && victim->position == POS_SHOVE )
        victim->position = POS_STANDING;
    if ( char_died(ch) || !ch->in_room )
        return;
    WAIT_STATE(ch, 12);
    /* Remove protection from shove/drag if char shoves -- Blodkai */
    if ( BV_IS_SET(ch->in_room->room_flags, ROOM_SAFE)   
    &&   get_timer(ch, TIMER_SHOVEDRAG) <= 0 )
      add_timer( ch, TIMER_SHOVEDRAG, 10, NULL, 0 );
}

void do_drag( CHAR_DATA *ch, char *argument )
{
    std::string arg;
    std::string arg2;
    std::string argstr = argument ? argument : "";
    int exit_dir;
    CHAR_DATA *victim;
    EXIT_DATA *pexit;
    ROOM_INDEX_DATA *to_room;
    bool nogo;
    int chance;
   ROOM_INDEX_DATA *fromroom;
   SHIP_DATA *ship;

    if ( !ch || !ch->in_room )
        return;

    argstr = one_argument( argstr, arg );
    argstr = one_argument( argstr, arg2 );

    if ( arg.empty() )
    {
	send_to_char( "Drag whom?\n", ch);
	return;
    }

    if ( ( victim = get_char_room( ch, arg ) ) == NULL )
    {
	send_to_char( "They aren't here.\n", ch);
	return;
    }

    if ( victim == ch )
    {
	send_to_char("You take yourself by the scruff of your neck, but go nowhere.\n", ch);
	return; 
    }

    if ( IS_NPC(victim) )
    {
	send_to_char("You can only drag player characters.\n", ch);
	return;
    }

    if ( victim->fighting )
    {
        send_to_char( "You try, but can't get close enough.\n", ch);
        return;
    }
          
    if ( arg2.empty() )
    {
	send_to_char( "Drag them in which direction?\n", ch);
	return;
    }

    const bool entering_ship = !str_cmp(arg2, "in");
    const bool leaving_ship = !str_cmp(arg2, "out");
    exit_dir = entering_ship || leaving_ship ? -1 : get_dir(arg2);

    if ( BV_IS_SET(victim->in_room->room_flags, ROOM_SAFE)
    &&   get_timer( victim, TIMER_SHOVEDRAG ) <= 0)
    {
	send_to_char("That character cannot be dragged right now.\n", ch);
	return;
    }

    nogo = FALSE;
    pexit = exit_dir < 0 ? nullptr : get_exit(ch->in_room, exit_dir);
    if ( !pexit )
    {
      if ( entering_ship )
      {
        if ( argstr.empty() )
        {
          send_to_char( "Drag him into what?\n", ch );
          return;
        }

        if ( ( ship = ship_in_room( ch->in_room , argstr ) ) == NULL )
        {
          act( AT_PLAIN, "I see no $T here.", ch, NULL, argstr, TO_CHAR );
          return;
        }
        
	if ( ch->mount )
   	{
          act( AT_PLAIN, "You can't go in there riding THAT.", ch, NULL, argstr, TO_CHAR );
          return;
   	} 

   	fromroom = ch->in_room;

        if ( ( to_room = get_room_index( ship->entrance ) ) != NULL )
   	{
   	   if ( ! ship->hatchopen )
   	   {
   	      send_to_char( "&RThe hatch is closed!\n", ch);
   	      return;
   	   }

           if ( to_room->tunnel > 0 )
           {
	        CHAR_DATA *ctmp;
	        int count = 0;

	       for ( ctmp = to_room->first_person; ctmp; ctmp = ctmp->next_in_room )
                   ++count;
	       if ( count + 2 > to_room->tunnel )
	       {
                  send_to_char( "There is no room for you both in there.\n", ch );
		  return;
	       }
           }
            if ( ship->shipstate == SHIP_LAUNCH || ship->shipstate == SHIP_LAUNCH_2 )
            {
                 send_to_char("&rThat ship has already started launching!\n",ch);
                 return;
            }
            
            act( AT_PLAIN, "$n enters $T.", ch,
		NULL, ship->name ? ship->name : "the ship" , TO_ROOM );
	    act( AT_PLAIN, "You enter $T.", ch,
		NULL, ship->name ? ship->name : "the ship" , TO_CHAR );
   	    char_from_room( ch );
   	    char_to_room( ch , to_room );
   	    act( AT_PLAIN, "$n enters the ship.", ch,
		NULL, argstr , TO_ROOM );
            act( AT_PLAIN, "$n enters $T.", victim,
		NULL, ship->name ? ship->name : "the ship" , TO_ROOM );
	    act( AT_PLAIN, "You enter $T.", victim,
		NULL, ship->name ? ship->name : "the ship" , TO_CHAR );
   	    char_from_room( victim );
   	    char_to_room( victim , to_room );
   	    act( AT_PLAIN, "$n enters the ship.", victim,
		NULL, argstr , TO_ROOM );
            do_look(ch, "auto");
            if ( !char_died(victim) && victim->in_room == to_room )
                do_look(victim, "auto");
	    return;
        }
        else
        {
          send_to_char("That ship has no entrance!\n", ch);
          return;
        }
      }  
      if ( leaving_ship )
      {
    	fromroom = ch->in_room;
    
	    if  ( (ship = ship_from_entrance(ch->game, fromroom->vnum)) == NULL )
	    {
	        send_to_char( "I see no exit here.\n" , ch );
	        return;
	    }   
        
	if ( ch->mount )
   	{
          act( AT_PLAIN, "You can't go out there riding THAT.", ch, NULL, argstr, TO_CHAR );
          return;
   	} 

    if ( ship->lastdoc != ship->location )
    {
        send_to_char("&rMaybe you should wait until the ship lands.\n",ch);
        return;
    }
    
    if ( ship->shipstate != SHIP_LANDED && ship->shipstate != SHIP_DISABLED )
    {
        send_to_char("&rPlease wait till the ship is properly docked.\n",ch);
        return;
    }
    
    if ( ! ship->hatchopen )
    {
    	send_to_char("&RYou need to open the hatch first" , ch );
    	return;
    }
    
    if ( ( to_room = get_room_index( ship->location ) ) != NULL )
    {

           if ( to_room->tunnel > 0 )
           {
	        CHAR_DATA *ctmp;
	        int count = 0;

	       for ( ctmp = to_room->first_person; ctmp; ctmp = ctmp->next_in_room )
                   ++count;
	       if ( count + 2 > to_room->tunnel )
	       {
                  send_to_char( "There is no room for you both in there.\n", ch );
		  return;
	       }
           }
            if ( ship->shipstate == SHIP_LAUNCH || ship->shipstate == SHIP_LAUNCH_2 )
            {
                 send_to_char("&rThat ship has already started launching!\n",ch);
                 return;
            }
            
            act( AT_PLAIN, "$n exits the ship.", ch,
		NULL, ship->name ? ship->name : "the ship" , TO_ROOM );
	    act( AT_PLAIN, "You exits the ship.", ch,
		NULL, ship->name ? ship->name : "the ship" , TO_CHAR );
   	    char_from_room( ch );
   	    char_to_room( ch , to_room );
   	    act( AT_PLAIN, "$n exits $T.", ch,
		NULL, ship->name ? ship->name : "the ship" , TO_ROOM );
            act( AT_PLAIN, "$n exits the ship.", victim,
		NULL, ship->name ? ship->name : "the ship" , TO_ROOM );
	    act( AT_PLAIN, "You exits the ship.", victim,
		NULL, ship->name ? ship->name : "the ship" , TO_CHAR );
   	    char_from_room( victim );
   	    char_to_room( victim , to_room );
   	    act( AT_PLAIN, "$n exits $T.", victim,
		NULL, ship->name ? ship->name : "the ship" , TO_ROOM );
            do_look(ch, "auto");
            if ( !char_died(victim) && victim->in_room == to_room )
                do_look(victim, "auto");
	    return;
        }
        else
        {
          send_to_char("That ship has no entrance!\n", ch);
          return;
        }
      }  
      nogo = TRUE;
    }
    else
    if ( IS_SET(pexit->exit_info, EX_CLOSED)
       && ( !( IS_AFFECTED( ch, AFF_PASS_DOOR ) && IS_AFFECTED( victim, AFF_PASS_DOOR ) )
       || IS_SET( pexit->exit_info, EX_NOPASSDOOR ) ) )
      nogo = TRUE;
    if ( nogo )
    {
	send_to_char( "There's no exit in that direction.\n", ch );
	return;
    }

    to_room = pexit->to_room;
    if ( !to_room )
    {
        send_to_char("There's no exit in that direction.\n", ch);
        return;
    }

    if (ch->in_room->area != to_room->area
    && !in_hard_range( victim, to_room->area ) )
    {
      send_to_char("That character cannot enter that area.\n", ch);
      return;
    }
    
    chance = 50;
    

if (chance < number_percent( ))
{
  send_to_char("You failed.\n", ch);
  return;
}
    if ( victim->position < POS_STANDING )
    {
	sh_int temp;

	temp = victim->position;
	victim->position = POS_DRAG;
	act( AT_ACTION, "You drag $M into the next room.", ch, NULL, victim, TO_CHAR ); 
	act( AT_ACTION, "$n grabs your hair and drags you.", ch, NULL, victim, TO_VICT ); 
        fromroom = ch->in_room;
	move_char(victim, pexit, 0);
	if ( !char_died(victim) && victim->position == POS_DRAG )
	  victim->position = temp;
/* Move ch to the room too.. they are doing dragging - Scryn */
        if ( char_died(ch) || ch->in_room != fromroom )
            return;
        if ( !char_died(victim) && victim->in_room && victim->in_room != fromroom )
            move_char(ch, get_exit(fromroom, exit_dir), 0);
        if ( !char_died(ch) )
            WAIT_STATE(ch, 12);
	return;
    }
    send_to_char("You cannot do that to someone who is standing.\n", ch);
    return;
}

void do_enlist( CHAR_DATA *ch, char *argument )
{

	CLAN_DATA *clan;
        
	if ( IS_NPC(ch) || !ch->pcdata )
	{
	    send_to_char( "You can't do that.\n", ch );
	    return;
	}
        
	if ( ch->pcdata->clan )
	{
		ch_printf( ch , "You will have to resign from %s before you can join a new organization.\n",
			ch->pcdata->clan->name ? ch->pcdata->clan->name : "your current organization" );
		return;
	}
        
	if ( !ch->in_room || ! BV_IS_SET( ch->in_room->room_flags , ROOM_RECRUIT ) )
	{
		send_to_char( "You don't seem to be in a recruitment office.\n", ch );
		return;
	}
	
	for ( clan = first_clan; clan; clan = clan->next )
	{
		if ( ( ch->in_room->vnum == clan->enlistroom1 ||
		       ch->in_room->vnum == clan->enlistroom2 ) )
		{
		  if ( !clan->name || clan->name[0] == '\0' )
		      continue;
		  if( !str_cmp( clan->name, "The Empire" ) && ch->race != RACE_HUMAN && ch->race != RACE_DEFEL )
		  {
		    send_to_char( "&CThe recruiter says, 'You will need to find a sponsor to enlist'&R&w\n", ch );
		    return;
		  }
			BV_SET_BIT( ch->speaks, LANG_CLAN );
			++clan->members;
			if ( ch->pcdata->clan_name )
				STRFREE( ch->pcdata->clan_name );
			ch->pcdata->clan_name = STRALLOC( clan->name );
			ch->pcdata->clan = clan;
			ch_printf( ch, "Welcome to %s.\n", clan->name );
			update_member( ch );
			save_clan(clan);
			save_char_obj(ch);
			return;
		}
	}

	send_to_char( "They don't seem to be recruiting right now.\n", ch );
	return;

}

void do_resign( CHAR_DATA *ch, char *argument )
{
 
       	CLAN_DATA *clan;
        long lose_exp;    
            
        if ( IS_NPC(ch) || !ch->pcdata )
	{
	    send_to_char( "You can't do that.\n", ch );
	    return;
	}
        
        clan =  ch->pcdata->clan;
        
        if ( clan == NULL )
        {
	    send_to_char( "You have to join an organization before you can quit it.\n", ch );
	    return;
	}

       if ( ch->name && ch->pcdata->clan->leader && !str_cmp( ch->name, ch->pcdata->clan->leader ) )
       {
           ch_printf( ch, "You can't resign from %s ... you are the leader!\n", clan->name ? clan->name : "(unnamed)" );
           return;
       }
       
    if ( ch->speaking == LANG_CLAN )
      ch->speaking = LANG_COMMON;
    BV_REMOVE_BIT( ch->speaks, LANG_CLAN );
    --clan->members;
    if ( ch->name && ch->pcdata->clan->number1 && !str_cmp( ch->name, ch->pcdata->clan->number1 ) )
    {
	STRFREE( ch->pcdata->clan->number1 );
	ch->pcdata->clan->number1 = STRALLOC( "" );
    }
    if ( ch->name && ch->pcdata->clan->number2 && !str_cmp( ch->name, ch->pcdata->clan->number2 ) )
    {
	STRFREE( ch->pcdata->clan->number2 );
	ch->pcdata->clan->number2 = STRALLOC( "" );
    }
    remove_member( ch );
    ch->pcdata->clan = NULL;
    if ( ch->pcdata->clan_name )
        STRFREE(ch->pcdata->clan_name);
    ch->pcdata->clan_name = STRALLOC( "" );
    act( AT_MAGIC, "You resign your position in $t", ch, clan->name ? clan->name : "(unnamed)", NULL , TO_CHAR );
    
    lose_exp = UMAX( ch->experience[DIPLOMACY_ABILITY] - exp_level( ch->skill_level[DIPLOMACY_ABILITY]  ) , 0 );
    ch_printf( ch, "You lose %ld diplomacy experience.\n", lose_exp ); 
    ch->experience[DIPLOMACY_ABILITY] -= lose_exp; 

    if ( ch->pcdata->bestowments )
        STR_DISPOSE( ch->pcdata->bestowments );
    ch->pcdata->bestowments = str_dup("");

    save_clan(clan);
    save_char_obj(ch);
    
    return;

}

static bool transfer_clan_funds(CHAR_DATA *ch, CLAN_DATA *clan, long change)
{
    const int old_gold = ch->gold;
    const long old_funds = clan->funds;
    ch->gold = static_cast<int>(ch->gold + static_cast<long long>(change));
    clan->funds -= change;
    const bool clan_saved = save_clan_file(clan);
    if ( clan_saved && save_char_obj(ch) )
        return true;

    ch->gold = old_gold;
    clan->funds = old_funds;
    if ( clan_saved && !save_clan_file(clan) )
        bug("Could not restore clan funds for %s", clan->name ? clan->name : "(unnamed)");
    send_to_char("Unable to save the clan transfer.\n", ch);
    return false;
}

void do_clan_withdraw( CHAR_DATA *ch, char *argument )
{
    CLAN_DATA *clan;
    long       amount;
    OBJ_DATA *obj;
    bool ch_comlink = FALSE;
    
    if ( IS_NPC( ch ) || !ch->pcdata || !ch->pcdata->clan )
    {
	send_to_char( "You don't seem to belong to an organization to withdraw funds from...\n", ch );
	return;
    }

    if ( (ch->pcdata && ch->pcdata->bestowments
    &&    is_name("withdraw", ch->pcdata->bestowments))
    ||   (ch->name && ch->pcdata->clan->leader && !str_cmp( ch->name, ch->pcdata->clan->leader )) )
	;
    else
    {
   	send_to_char( "&RYour organization hasn't seen fit to bestow you with that ability." ,ch );
   	return;
    }

      if ( IS_IMMORTAL( ch ) )
          ch_comlink = TRUE;
      else
        for ( obj = ch->last_carrying; obj; obj = obj->prev_content )
        {
           if (obj->pIndexData->item_type == ITEM_COMLINK)
           ch_comlink = TRUE;
        }
    
      if ( !ch_comlink )
      {
        if (!ch->in_room || !BV_IS_SET(ch->in_room->room_flags, ROOM_BANK) )
        {
          send_to_char( "You must be in a bank or have a comlink to do that!\n", ch );
          return;
        }
      }


    clan = ch->pcdata->clan;
    
    if ( !parse_clan_amount(argument, amount) || amount == 0 )
    {
	send_to_char( "How much would you like to withdraw?\n", ch );
	return;
    }
    
    if ( amount > clan->funds )
    {
	ch_printf( ch,  "%s doesn't have that much!\n", clan->name ? clan->name : "Your organization" );
	return;
    }
    
    if ( amount < 0 )
    {
	ch_printf( ch,  "Nice try...\n" );
	return;
    }
    
    if ( amount > static_cast<long long>(INT_MAX) - ch->gold )
    {
        send_to_char("You cannot carry that many credits.\n", ch);
        return;
    }
    if ( transfer_clan_funds(ch, clan, amount) )
        ch_printf( ch,  "You withdraw %ld credits from %s's funds.\n", amount, clan->name ? clan->name : "your organization" );

}


void do_clan_donate( CHAR_DATA *ch, char *argument )
{
    CLAN_DATA *clan;
    long       amount;
    OBJ_DATA *obj;
    bool ch_comlink = FALSE;

    if ( IS_NPC( ch ) || !ch->pcdata || !ch->pcdata->clan )
    {
	send_to_char( "You don't seem to belong to an organization to donate to...\n", ch );
	return;
    }

      if ( IS_IMMORTAL( ch ) )
          ch_comlink = TRUE;
      else
        for ( obj = ch->last_carrying; obj; obj = obj->prev_content )
        {
           if (obj->pIndexData->item_type == ITEM_COMLINK)
           ch_comlink = TRUE;
        }
    
      if ( !ch_comlink )
      {
        if (!ch->in_room || !BV_IS_SET(ch->in_room->room_flags, ROOM_BANK) )
        {
          send_to_char( "You must be in a bank or have a comlink to do that!\n", ch );
          return;
        }
      }


    clan = ch->pcdata->clan;
    
    if ( !parse_clan_amount(argument, amount) || amount == 0 )
    {
	send_to_char( "How much would you like to donate?\n", ch );
	return;
    }

    if ( amount < 0 )
    {
	ch_printf( ch,  "Nice try...\n" );
	return;
    }
    
    if ( amount > ch->gold )
    {
	send_to_char( "You don't have that much!\n", ch );
	return;
    }
    
    if ( clan->funds > LONG_MAX - amount )
    {
        send_to_char("Your organization cannot hold that many credits.\n", ch);
        return;
    }
    if ( transfer_clan_funds(ch, clan, -amount) )
        ch_printf( ch,  "You donate %ld credits to %s's funds.\n", amount, clan->name ? clan->name : "your organization" );

}

void do_appoint ( CHAR_DATA *ch , char *argument )
{
    std::string arg;
    std::string argstr = argument ? argument : "";
    
    argstr = one_argument( argstr, arg );
    
    if ( IS_NPC( ch ) || !ch->pcdata )
      return;

    if ( !ch->pcdata->clan )
    {
	send_to_char( "Huh?\n", ch );
	return;
    }

    if ( !ch->name || !ch->pcdata->clan->leader || str_cmp( ch->name, ch->pcdata->clan->leader ) )
    {
	send_to_char( "Only your leader can do that!\n", ch );
	return;
    }

    if ( arg.empty() )
    {
	send_to_char( "Useage: appoint <name> < first | second >\n", ch );
	return;
    }
    
    if ( !str_cmp( argstr , "first" )  )
    {
         if ( ch->pcdata->clan->number1 && str_cmp( ch->pcdata->clan->number1 , "" ) )
         {
             send_to_char( "You already have someone in that position ... demote them first.\n", ch );
	     return;
	 }
         
         if ( ch->pcdata->clan->number1 )
             STRFREE( ch->pcdata->clan->number1 );
         ch->pcdata->clan->number1 = STRALLOC( arg );
    }        
    else if ( !str_cmp( argstr , "second" )  )
    {
         if ( ch->pcdata->clan->number2 && str_cmp( ch->pcdata->clan->number2 , "" ))
         {
             send_to_char( "You already have someone in that position ... demote them first.\n", ch );
	     return;
	 }
         
         if ( ch->pcdata->clan->number2 )
             STRFREE( ch->pcdata->clan->number2 );
         ch->pcdata->clan->number2 = STRALLOC( arg );
    }
    else do_appoint( ch , "" );
    save_clan ( ch->pcdata->clan );
        
}

void do_demote ( CHAR_DATA *ch , char *argument )
{
    
    if ( IS_NPC( ch ) || !ch->pcdata )
      return;

    if ( !ch->pcdata->clan )
    {
	send_to_char( "Huh?\n", ch );
	return;
    }

    if ( !ch->name || !ch->pcdata->clan->leader || str_cmp( ch->name, ch->pcdata->clan->leader ) )
    {
	send_to_char( "Only your leader can do that!\n", ch );
	return;
    }

    if ( !argument || argument[0] == '\0' )
    {
	send_to_char( "Demote who?\n", ch );
	return;
    }
    
    if ( ch->pcdata->clan->number1 && !str_cmp( argument , ch->pcdata->clan->number1 ) )
    {
         send_to_char( "Player Demoted!", ch );
         
         STRFREE( ch->pcdata->clan->number1 );
         ch->pcdata->clan->number1 = STRALLOC( "" );
    }        
    else if ( ch->pcdata->clan->number2 && !str_cmp( argument , ch->pcdata->clan->number2 ) )
    {
         send_to_char( "Player Demoted!", ch );
         
         STRFREE( ch->pcdata->clan->number2 );
         ch->pcdata->clan->number2 = STRALLOC( "" );
    }        
    else
    {
	send_to_char( "They seem to have been demoted already.\n", ch );
	return;
    }
    save_clan ( ch->pcdata->clan );
        
}

void do_capture ( CHAR_DATA *ch , char *argument )
{
   CLAN_DATA *clan;
   PLANET_DATA *planet;
   PLANET_DATA *cPlanet;
   float support = 0.0;
// int pCount = 0;   
   
   if ( !ch->in_room || !ch->in_room->area)
      return;   

   if ( IS_NPC(ch) || !ch->pcdata )
   {
       send_to_char ( "huh?\n" , ch );
       return;
   }
   
   if ( !ch->pcdata->clan )
   {
       send_to_char ( "You need to be a member of an organization to do that!\n" , ch );
       return;
   }
   
   if ( ch->pcdata->clan->mainclan )
      clan = ch->pcdata->clan->mainclan;
   else
      clan = ch->pcdata->clan;
      
   if ( clan->clan_type == CLAN_CRIME )
   {
       send_to_char ( "Crime fimilies aren't in the business of controlling worlds.\n" , ch );
       return;
   }

   if ( clan->clan_type == CLAN_GUILD )
   {
       send_to_char ( "Your organization serves a much greater purpose.\n" , ch );
       return;
   }

   if ( ( planet = ch->in_room->area->planet ) == NULL )
   {
       send_to_char ( "You must be on a planet to capture it.\n" , ch );
       return;
   }   
   
   if ( clan == planet->governed_by )
   {
       send_to_char ( "Your organization already controls this planet.\n" , ch );
       return;
   }
   
   if ( planet->spaceobject )
   {
       SHIP_DATA *ship;
       CLAN_DATA *sClan;
              
       for ( ship = first_ship ; ship ; ship = ship->next )
       {
	  if( !ship->spaceobject )
	    continue;
       	  if( ship->shipstate == SHIP_HYPERSPACE || ship->shipstate == SHIP_DISABLED )
       	    continue;
       	  if( !space_in_range_c( ship, planet->spaceobject ) )
       	    continue;
          if ( !ship->owner || ship->owner[0] == '\0' )
             continue;
          sClan = get_clan(ship->owner);
          if ( !sClan ) 
             continue;
          if ( sClan->mainclan )
             sClan = sClan->mainclan;
          if ( sClan == planet->governed_by )
          {
             send_to_char ( "A planet cannot be captured while protected by orbiting spacecraft.\n" , ch );
             return;
          }
       }
   }

   if ( IS_SET( planet->flags, PLANET_NOCAPTURE ) )
   {
       send_to_char ( "This planet cannot be captured.\n" , ch);
       return;
   }

   if ( planet->pop_support > 0 )
   {
       send_to_char ( "The population is not in favour of changing leaders right now.\n" , ch );
       return;
   }
   
   for ( cPlanet = first_planet ; cPlanet ; cPlanet = cPlanet->next )
        if ( clan == cPlanet->governed_by )
        {
//          pCount++;
            support += cPlanet->pop_support;
        }
   
   if ( support < 0 )
   {
       send_to_char ( "There is not enough popular support for your organization!\nTry improving loyalty on the planets that you already control.\n" , ch );
       return;
   }
   
   planet->governed_by = clan;
   planet->pop_support = 50;
   
   echo_to_all( AT_RED,
       str_printf( "%s has claimed the planet %s!",
           clan->name ? clan->name : "(unnamed)",
           planet->name ? planet->name : "(unnamed)" ),
       0 );
   
   save_planet( planet );
      
   return; 
}

void do_empower ( CHAR_DATA *ch , char *argument )
{
    std::string arg;
    std::string arg2;
    std::string argstr = argument ? argument : "";
    CHAR_DATA *victim;
    CLAN_DATA *clan;

    if ( IS_NPC( ch ) || !ch->pcdata || !ch->pcdata->clan )
    {
	send_to_char( "Huh?\n", ch );
	return;
    }

    clan = ch->pcdata->clan;

    if ( (ch->pcdata && ch->pcdata->bestowments
    &&    is_name("empower", ch->pcdata->bestowments))
    || (ch->name && clan->leader && !str_cmp( ch->name, clan->leader )) )
	;
    else
    {
	send_to_char( "You clan hasn't seen fit to bestow that ability to you!\n", ch );
	return;
    }

    argstr = one_argument( argstr, arg );
    argstr = one_argument( argstr, arg2 );

    if ( arg.empty() )
    {
	send_to_char( "Empower whom to do what?\n", ch );
	return;
    }

    if ( ( victim = get_char_world( ch, arg ) ) == NULL )
    {
	send_to_char( "That player is not here.\n", ch);
	return;
    }

    if ( IS_NPC(victim) )
    {
	send_to_char( "Not on NPC's.\n", ch );
	return;
    }

    if ( victim == ch )
    {
	    send_to_char( "Nice try.\n", ch );
	    return;
    }
 
    if ( victim->pcdata->clan != ch->pcdata->clan )
    {
	    send_to_char( "This player does not belong to your clan!\n", ch );
	    return;
    }

    if (!victim->pcdata->bestowments)
      victim->pcdata->bestowments = str_dup("");

    if ( arg2.empty() || !str_cmp( arg2, "list" ) )
    {
        ch_printf( ch, "Current bestowed commands on %s: %s.\n",
                      victim->name ? victim->name : "Someone", victim->pcdata->bestowments );
        ch_printf( ch, "Current salary on %s: %d.\n",
                      victim->name ? victim->name : "Someone", victim->pcdata->salary );

        return;
    }
    
    if ( (victim->pcdata && victim->pcdata->bestowments
    &&    is_name(arg2, victim->pcdata->bestowments)) )
    {
	    send_to_char( "That player already has that power.\n", ch );
	    return;
    }

   if( !ch->name || !clan->leader || str_cmp( ch->name, clan->leader ) )
   {
      if( !ch->pcdata->bestowments || !is_name( arg2, ch->pcdata->bestowments ) )
      {
         send_to_char( "&RI don't think you're even allowed to do that.&W\n", ch );
         return;
      }
   }    
    

    if ( !str_cmp( arg2, "none" ) )
    {
        if ( victim->pcdata->bestowments )
            STR_DISPOSE( victim->pcdata->bestowments );
        victim->pcdata->bestowments = str_dup("");
        ch_printf( ch, "Bestowments removed from %s.\n", victim->name ? victim->name : "Someone" );
        ch_printf( victim, "%s has removed your bestowed clan abilities.\n", ch->name ? ch->name : "Someone" );
        save_char_obj( victim );
        return;
    }

    struct EmpowerEntry
    {
        const char *ability;
        const char *vict_msg;
        const char *ch_msg;
    };
    static const EmpowerEntry empower_table[] = {
        { "pilot",       "has given you permission to fly clan ships.",         "Ok, they now have the ability to fly clan ships.\n" },
        { "withdraw",    "has given you permission to withdraw clan funds.",    "Ok, they now have the ablitity to withdraw clan funds.\n" },
        { "clanbuyship", "has given you permission to buy clan ships.",         "Ok, they now have the ablitity to use clanbuyship.\n" },
        { "induct",      "has given you permission to induct new members.",     "Ok, they now have the ablitity to induct new members.\n" },
        { "empower",     "has given you permission to empower members.",        "Ok, they now have the ablitity to empower members.\n" },
        { "salary",      "has given you permission to assign salaries.",        "Ok, they now have the ablitity to assign salaries.\n" },
        { "roster",      "has given you permission to access the roster.",      "Ok, they now have the ablitity to access the roster.\n" },
    };

    bool matched = false;
    for ( const auto& entry : empower_table )
    {
        if ( !str_cmp( arg2, entry.ability ) )
        {
            std::string current = victim->pcdata->bestowments ? victim->pcdata->bestowments : "";
            std::string updated = current.empty() ? std::string(entry.ability) : (current + " " + entry.ability);
            if ( victim->pcdata->bestowments )
                STR_DISPOSE( victim->pcdata->bestowments );
            victim->pcdata->bestowments = str_dup( updated );
            ch_printf( victim, "%s %s\n", ch->name ? ch->name : "Someone", entry.vict_msg );
            send_to_char( entry.ch_msg, ch );
            matched = true;
            break;
        }
    }
    if ( !matched )
    {
      send_to_char( "Currently you may empower members with only the following:\n", ch ); 
      send_to_char( "\npilot:       ability to fly clan ships\n", ch );
      send_to_char(     "withdraw:    ability to withdraw clan funds\n", ch );
      send_to_char(     "clanbuyship: ability to buy clan ships\n", ch );    
      send_to_char(     "induct:      ability to induct new members\n", ch );    
      send_to_char(     "empower:     ability to empower members\n", ch );
      send_to_char(     "salary:      ability to assign salaries\n", ch );    
      send_to_char(     "roster:      ability to see the full clan roster\n", ch );
      send_to_char(     "none:        removes bestowed abilities\n", ch );    
    }
    
    save_char_obj( victim );	/* clan gets saved when pfile is saved */
    return;


}

float get_taxes( PLANET_DATA *planet )
{
      float gain;
      
      gain = planet->base_value;
      gain += planet->base_value*planet->pop_support/100;
      gain += UMAX(0, planet->pop_support/10 * planet->population);
      
      return gain;
}

/*
    (link)->prev		= (insert)->prev;		
    if ( !(insert)->prev )					
      (first)			= (link);			
    else							
      (insert)->prev->next	= (link);			
    (insert)->prev		= (link);			
    (link)->next		= (insert);			
*/


void do_addsalary ( CHAR_DATA *ch , char *argument )
{
    std::string arg;
    std::string arg2;
    std::string argstr = argument ? argument : "";
    CHAR_DATA *victim;
    CLAN_DATA *clan;
    long salary;
//    bool info = FALSE;

    if ( IS_NPC( ch ) || !ch->pcdata || !ch->pcdata->clan )
    {
	send_to_char( "Huh?\n", ch );
	return;
    }

    clan = ch->pcdata->clan;

    if ( (ch->pcdata && ch->pcdata->bestowments
    &&    is_name("salary", ch->pcdata->bestowments))
    || (ch->name && clan->leader && !str_cmp( ch->name, clan->leader )) )
	;
    else
    {
	send_to_char( "You clan hasn't seen fit to bestow that ability to you!\n", ch );
	return;
    }

    argstr = one_argument( argstr, arg );
    argstr = one_argument( argstr, arg2 );
//    if ( arg2.empty() )
//      info = TRUE;
    
    

    if ( arg.empty() )
    {
	send_to_char( "Assign a salary to whom?\n", ch );
	return;
    }

    if ( ( victim = get_char_world( ch, arg ) ) == NULL )
    {
	send_to_char( "That player is not here.\n", ch);
	return;
    }

    if ( IS_NPC(victim) || !victim->pcdata )
    {
	send_to_char( "Not on NPC's.\n", ch );
	return;
    }

    if ( victim == ch )
    {
	    send_to_char( "Nice try.\n", ch );
	    return;
    }
 
    if ( victim->pcdata->clan != ch->pcdata->clan )
    {
	    send_to_char( "This player does not belong to your clan!\n", ch );
	    return;
    }

    if ( !parse_clan_amount(arg2.c_str(), salary) || salary < 0 || salary > INT_MAX )
    {
        ch_printf( ch, "Salary's must be positive!\n" );
        return;
    }

        const int old_salary = victim->pcdata->salary;
        victim->pcdata->salary = static_cast<int>(salary);
        if ( !save_char_obj(victim) )
        {
            victim->pcdata->salary = old_salary;
            send_to_char("Unable to save that salary.\n", ch);
            return;
        }
        ch_printf( ch, "%s has been assigned %ld credits for a salary.\n", victim->name ? victim->name : "Someone", salary );
        ch_printf( victim, "%s has given you a %ld credit salary.\n", ch->name ? ch->name : "Someone", salary );
        return;

}    

void show_members( CHAR_DATA *ch, const std::string& argument, const std::string& format )
{
     MEMBER_LIST	*members_list;
     MEMBER_DATA	*member;
     CLAN_DATA	*clan;
     int members = 0;

     if ( !ch )
         return;

     for( members_list = first_member_list; members_list; members_list = members_list->next )
     {
         if( members_list->name && !str_cmp( members_list->name, argument ) )
             break;
     }

     if( !members_list )
         return;

     clan = get_clan( argument );

     if ( !clan  )
         return;

     auto print_member_line = [&]( MEMBER_DATA *m ) {
         const char *mname = m->name ? m->name : "";
         const char *cname = (m->plrclass >= 0 && m->plrclass < MAX_ABILITY)
             ? ability_name[m->plrclass] : "Unknown";
         const char *msince = m->since ? m->since : "Unknown";
         const char *mlaston = m->laston ? m->laston : "Unknown";
         pager_printf( ch, "[%3d] %12s %15s %5d %6d %10s %10s\n",
                       m->level,
                       capitalize( mname ).c_str(),
                       cname,
                       m->kills,
                       m->deaths,
                       msince,
                       mlaston );
     };

     const char *leader = clan->leader ? clan->leader : "";
     const char *num1 = clan->number1 ? clan->number1 : "";
     const char *num2 = clan->number2 ? clan->number2 : "";

    pager_printf( ch, "\nMembers of %s\n", clan->name ? clan->name : "" );
    pager_printf( ch, 
"------------------------------------------------------------\n" );
    pager_printf( ch, "Leader: %s\n", leader );
    pager_printf( ch, "Number1: %s\n", num1 );
    pager_printf( ch, "Number2: %s\n", num2 );
    pager_printf( ch, "Spacecraft: %d  Vehicles: %d\n", clan->spacecraft, clan->vehicles );
    pager_printf( ch, 
"------------------------------------------------------------\n" );
    pager_printf( ch, "  Lvl         Name           Class Kills Deaths       Joined      Last On\n\n" );

     if( !format.empty() )
     {
         if( !str_cmp( format, "kills" )
             || !str_cmp( format, "deaths" )
             || !str_cmp( format, "alpha" ))
         {
             if ( members_list->first_member )
             {
                 MS_DATA *insert = NULL;
                 MS_DATA *sort, *sort_next;
                 MS_DATA *first_member = NULL;
                 MS_DATA *last_member = NULL;

                 CREATE( sort, MS_DATA, 1 );
                 sort->member = members_list->first_member;
                 LINK( sort, first_member, last_member, next, prev );

                 for( member = members_list->first_member->next; member; member = member->next )
                 {
                     insert = NULL;
                     for( sort = first_member; sort; sort = sort->next )
                     {
                         if( !str_cmp( format, "kills" ))
                         {
                             if( member->kills > sort->member->kills )
                             {
                                 CREATE( insert, MS_DATA, 1 );
                                 insert->member = member;
                                 INSERT( insert, sort, first_member, next, prev );
                                 break;
                             }
                         }
                         else if( !str_cmp( format, "deaths" ))
                         {
                             if( member->deaths > sort->member->deaths )
                             {
                                 CREATE( insert, MS_DATA, 1 );
                                 insert->member = member;
                                 INSERT( insert, sort, first_member, next, prev );
                                 break;
                             }
                         }
                         else if( !str_cmp( format, "alpha" ))
                         {
                             const char *a = member->name ? member->name : "";
                             const char *b = sort->member->name ? sort->member->name : "";
                             if( strcmp( a, b ) < 0 )
                             {
                                 CREATE( insert, MS_DATA, 1 );
                                 insert->member = member;
                                 INSERT( insert, sort, first_member, next, prev );
                                 break;
                             }
                         }
                     }
                     if( insert == NULL )
                     {
                         CREATE( insert, MS_DATA, 1 );
                         insert->member = member;
                         LINK( insert, first_member, last_member, next, prev );
                     }
                 }

                 for( sort = first_member; sort; sort = sort->next )
                 {
                     const char *sname = sort->member->name ? sort->member->name : "";
                     if( str_cmp( sname, leader )
                         && str_cmp( sname, num1 )
                         && str_cmp( sname, num2 ) )
                     {
                         members++;
                         print_member_line( sort->member );
                     }
                 }

                 for( sort = first_member; sort; sort = sort_next )
                 {
                     sort_next = sort->next;
                     UNLINK( sort, first_member, last_member, next, prev );
                     DISPOSE( sort );
                 }
             }
         }
         else
         {
             for( member = members_list->first_member; member; member = member->next )
             {
                 if( member->name && !str_prefix( format, member->name ) )
                 {
                     members++;
                     print_member_line( member );
                 }
             }
         }
     }
     else
     {
         for( member = members_list->first_member; member; member = member->next )
         {
             const char *mname = member->name ? member->name : "";
             if( str_cmp( mname, leader ) && str_cmp( mname, num1 )
                 && str_cmp( mname, num2 ) )
             {
                 members++;
                 print_member_line( member );
             }
         }
     }

             pager_printf( ch, 
"------------------------------------------------------------\n" );
	     pager_printf( ch, "Total Members: %d\n", members);
             pager_printf( ch, 
"------------------------------------------------------------\n" );


}

void remove_member( CHAR_DATA *ch )
{
    MEMBER_LIST	*members_list;
    MEMBER_DATA	*member;

    if( !ch || !ch->pcdata || !ch->pcdata->clan_name )
        return;

    for( members_list = first_member_list; members_list; members_list = members_list->next )
    {
      if( members_list->name && !str_cmp( members_list->name, ch->pcdata->clan_name ) )
        for( member = members_list->first_member; member; member = member->next )
        {
          if( member->name && ch->name && !str_cmp( member->name, ch->name ) )
          {
            UNLINK( member, members_list->first_member, members_list->last_member, next, prev );
            if ( member->name )
              STRFREE( member->name );
            if ( member->since )
              STRFREE( member->since );
            if ( member->laston )
              STRFREE( member->laston );
            DISPOSE( member );
            save_member_list( members_list );
            break;
          }
        }
    }
}

void do_roster( CHAR_DATA *ch, char *argument )
{
     if( IS_NPC( ch ) || !ch->pcdata || !ch->pcdata->clan
         || ( (!ch->name || !ch->pcdata->clan->leader || str_cmp(ch->name, ch->pcdata->clan->leader ))
              && (!ch->name || !ch->pcdata->clan->number1 || str_cmp(ch->name, ch->pcdata->clan->number1 ))
              && (!ch->name || !ch->pcdata->clan->number2 || str_cmp(ch->name, ch->pcdata->clan->number2 ))
              && (!ch->pcdata->bestowments
    || !is_name("roster", ch->pcdata->bestowments)) ) )
     {
         send_to_char( "Huh?\n", ch );
         return;
     }

     show_members( ch, ch->pcdata->clan->name ? ch->pcdata->clan->name : "", argument ? argument : "" );
     return;

}
void do_members( CHAR_DATA *ch, char *argument )
{
     std::string arg1;
     std::string argstr = argument ? argument : "";
     argstr = one_argument( argstr, arg1 );

     if( !ch )
         return;

     if( argstr.empty() || arg1.empty() )
     {
         send_to_char( "Do what?\n", ch );
         return;
     }

     if( !str_cmp( arg1, "show" ) )
     {
         if( !str_cmp( argstr, "all" ) )
         {
             MEMBER_LIST *members_list;
             for( members_list = first_member_list; members_list; members_list = members_list->next )
                 show_members( ch, members_list->name ? members_list->name : "", "" );
             return;
         }

         show_members( ch, argstr, "" );
         return;
     }

     if( !str_cmp( arg1, "create" ) )
     {
         MEMBER_LIST *members_list;

         CREATE( members_list, MEMBER_LIST, 1 );
         members_list->game = ch->game;
         members_list->name = STRALLOC( argstr );
         LINK( members_list, first_member_list, last_member_list, next, prev );
         save_member_list( members_list );
         ch_printf( ch, "Member lists \"%s\" created.\n", argstr.c_str() );
         return;
     }

     if( !str_cmp( arg1, "delete" ) )
     {
         MEMBER_LIST *members_list;
         MEMBER_DATA *member;

         for( members_list = first_member_list; members_list; members_list = members_list->next )
             if( members_list->name && !str_cmp( argstr, members_list->name ) )
             {
                 while( members_list->first_member )
                 {
                     member = members_list->first_member;
                     if ( member->name )
                       STRFREE( member->name );
                     if ( member->since )
                       STRFREE( member->since );
                     if ( member->laston )
                       STRFREE( member->laston );
                     UNLINK( member, members_list->first_member, members_list->last_member, next, prev );
                     DISPOSE( member );
                 }

                 if ( members_list->name )
                   STRFREE( members_list->name );
                 UNLINK( members_list, first_member_list, last_member_list, next, prev );
                 DISPOSE( members_list );
                 ch_printf( ch, "Member list \"%s\" destroyed.\n", argstr.c_str() );
                 return;
             }
         send_to_char( "No such list.\n", ch );
         return;
     }
}

void save_member_list( MEMBER_LIST *members_list )
{
    if ( !members_list )
    {
        bug( "save_member_list: NULL members_list" );
        return;
    }

    CLAN_DATA *clan = members_list->name ? get_clan( members_list->name ) : nullptr;
    if ( !clan )
    {
        bug( "save_member_list: no such clan: %s", members_list->name ? members_list->name : "(null)" );
        return;
    }

    save_member_list_file( clan, members_list );
}

static bool save_member_list_file( const CLAN_DATA *clan, const MEMBER_LIST *members_list )
{
    if ( !clan->filename || !realm_filename_valid( clan->filename ) )
    {
        bug( "save_member_list: clan %s has no valid filename", clan->name ? clan->name : "(null)" );
        return false;
    }

    const std::string path = std::string( CLAN_DIR ) + clan->filename + ".mem";
    return write_realm_file( path, [&]( FILE *fp ) {
        fprintf( fp, "Version       2\n" );
        fprintf( fp, "Name          %s~\n", members_list->name );
        for ( const MEMBER_DATA *member = members_list->first_member; member; member = member->next )
            fprintf( fp, "Member        %s %s %d %d %d %d %s\n",
                     member->name ? member->name : "Unknown",
                     member->since ? member->since : "Unknown",
                     member->kills, member->deaths, member->level, member->plrclass,
                     member->laston ? member->laston : "Unknown" );
        fprintf( fp, "End\n\n" );
        return true;
    } );
}

bool load_member_list( GameContext *game, const std::string& filename )
{
     FILE *fp;
     std::string buf;
     MEMBER_LIST *members_list;
     MEMBER_DATA *member;
     int version = 1;

     buf = str_printf("%s%s.mem", CLAN_DIR, filename.c_str());

     if( ( fp = fopen( buf.c_str(), "r" ) ) == NULL )
     {
         bug( "Cannot open member list for reading" );
         return FALSE;
     }

     CREATE( members_list, MEMBER_LIST, 1 );
     members_list->game = game;

     auto cleanup_partial = [&]() {
         while ( members_list->first_member )
         {
             MEMBER_DATA *m = members_list->first_member;
             UNLINK( m, members_list->first_member, members_list->last_member, next, prev );
             if ( m->name )
                 STRFREE( m->name );
             if ( m->since )
                 STRFREE( m->since );
             if ( m->laston )
                 STRFREE( m->laston );
             DISPOSE( m );
         }
         if ( members_list->name )
             STRFREE( members_list->name );
         DISPOSE( members_list );
         FCLOSE( fp );
     };

     for( ; ; )
     {
         char *word;

         if ( feof( fp ) )
         {
             bug( "load_member_list: unexpected EOF" );
             cleanup_partial();
             return FALSE;
         }

         word = fread_word( fp );
         if ( !word || word[0] == '\0' )
         {
             bug( "load_member_list: unexpected EOF" );
             cleanup_partial();
             return FALSE;
         }

         if( !str_cmp( word, "Version" ) )
         {
             version = fread_number( fp );
             continue;
         }
         else
         if( !str_cmp( word, "Name" ) )
         {
             if ( members_list->name )
                 STRFREE( members_list->name );
             members_list->name = fread_string( fp );
             continue;
         }
         else
         if( !str_cmp( word, "Member" ) )
         {
             CREATE( member, MEMBER_DATA, 1 );
             member->game = game;
             member->name = STRALLOC( fread_word( fp ) );
             member->since = STRALLOC( fread_word( fp ) );
             member->kills = fread_number( fp );
             member->deaths = fread_number( fp );
             member->level = fread_number( fp );
             member->plrclass = fread_number( fp );
             if ( version == 2 )
                 member->laston = STRALLOC( fread_word( fp ) );
             else
                 member->laston = STRALLOC( "Unknown" );
             
             LINK( member, members_list->first_member, members_list->last_member, next, prev );
             continue;
         }
         else
         if( !str_cmp( word, "End" ) )
         {
             if ( !members_list->name )
                 members_list->name = STRALLOC( filename );
             LINK( members_list, first_member_list, last_member_list, next, prev );
             FCLOSE( fp );
             return TRUE;
         }
         else
         {
             bug( "load_members_list: bad section" );
             cleanup_partial();
             return FALSE;
         }
     }

}

void update_member( CHAR_DATA *ch )
{
     MEMBER_LIST *members_list;
     MEMBER_DATA *member;
     struct tm *t = localtime(&current_time);
     std::string date_str = t
         ? str_printf("[%02d|%02d|%04d]", t->tm_mon+1, t->tm_mday, t->tm_year+1900)
         : "Unknown";

     if( !ch || IS_NPC( ch ) || IS_IMMORTAL(ch) || !ch->pcdata || !ch->pcdata->clan )
         return;

     for( members_list = first_member_list; members_list; members_list = members_list->next )
         if( members_list->name && ch->pcdata->clan_name && !str_cmp( members_list->name, ch->pcdata->clan_name ) )
         {
             for( member = members_list->first_member; member; member = member->next )
                 if ( member->name && ch->name && !str_cmp( member->name, ch->name ) )
                 {
                     if( ch->pcdata->clan->clan_type == CLAN_PLAIN )
                     {
                         member->kills = ch->pcdata->pkills;
                         member->deaths = ch->pcdata->clones;
                     }
                     else
                     {
                         member->kills = ch->pcdata->pkills;
                         member->deaths = ch->pcdata->clones;
                     }
                     member->plrclass = ch->main_ability;
                     member->level = ch->top_level;
                     if ( member->laston )
                         STRFREE( member->laston );
                     member->laston = STRALLOC( date_str );
                     gmcp_evt_char_status(ch);
                     save_member_list( members_list );
                     return;
                 }

             if( member == NULL )
             {
                 CREATE( member, MEMBER_DATA, 1 );
                 member->game = members_list->game ? members_list->game : ch->game;
                 member->name = STRALLOC( ch->name ? ch->name : "" );
                 member->level = ch->top_level;
                 member->plrclass = ch->main_ability;
                 member->since = STRALLOC( date_str );
                 member->laston = STRALLOC( date_str );
                 if( ch->pcdata->clan->clan_type == CLAN_PLAIN )
                 {
                     member->kills = ch->pcdata->pkills;
                     member->deaths = ch->pcdata->clones;
                 }
                 else
                 {
                     member->kills = ch->pcdata->pkills;
                     member->deaths = ch->pcdata->clones;
                 }
                 LINK( member, members_list->first_member, members_list->last_member, next, prev );
                 save_member_list( members_list );

             }

         }
    gmcp_evt_char_status(ch);

}
    
void do_clanfunds( CHAR_DATA *ch, char *argument )
{
  CLAN_DATA *clan;
  OBJ_DATA *obj;
  bool ch_comlink = FALSE;

  if ( IS_NPC( ch ) || !ch->pcdata || !ch->pcdata->clan )
  {
    send_to_char("You don't seem to belong to an organization.\n",ch);
    return;
  }

  if ( IS_IMMORTAL( ch ) )
    ch_comlink = TRUE;
  else
    for ( obj = ch->last_carrying; obj; obj = obj->prev_content )
    {
      if (obj->pIndexData->item_type == ITEM_COMLINK)
        ch_comlink = TRUE;
    }
  
  if ( !ch_comlink )
  {
    if (!ch->in_room || !BV_IS_SET(ch->in_room->room_flags, ROOM_BANK) )
    {
      send_to_char( "You must be in a bank or have a comlink to do that!\n", ch );
      return;
    }
  }

  clan = ch->pcdata->clan;
  if ( clan->funds == 0 )
  {
    ch_printf(ch,"%s has no funds at its disposal.",clan->name ? clan->name : "Your organization");
    return;
  }
  ch_printf(ch,"%s has %ld credits at its disposal.\n",clan->name ? clan->name : "Your organization",clan->funds);
}

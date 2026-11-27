#include <queue>
#include <set>
#include "mud.h"

static constexpr int map_width = 11;
static constexpr int map_height = 9;
static constexpr int max_depth = 4;

struct MapRoom
{
    ROOM_INDEX_DATA *room;
    int x;
    int y;
    int depth;
};

static void render_room_map( CHAR_DATA *ch, bool scanner )
{
    static constexpr int dx[] = {0, 1, 0, -1, 0, 0, 1, -1, 1, -1};
    static constexpr int dy[] = {-1, 0, 1, 0, 0, 0, -1, -1, 1, 1};
    static constexpr char connectors[] = "|-|-  /\\\\/";

    ROOM_INDEX_DATA *rooms[map_height][map_width] = {};
    std::string map[map_height];
    for ( auto &row : map )
        row.assign( map_width, ' ' );

    std::queue<MapRoom> pending;
    std::set<ROOM_INDEX_DATA *> placed;
    const int x = map_width / 2;
    const int y = map_height / 2;
    pending.push( {ch->in_room, x, y, 0} );
    placed.insert( ch->in_room );
    rooms[y][x] = ch->in_room;
    map[y][x] = 'X';

    while ( !pending.empty() )
    {
        const MapRoom current = pending.front();
        pending.pop();
        if ( current.depth == max_depth )
            continue;

        for ( int dir = 0; dir < static_cast<int>(sizeof(dx) / sizeof(dx[0])); ++dir )
        {
            if ( dx[dir] == 0 && dy[dir] == 0 ) // Vertical exits have no map cell.
                continue;
            EXIT_DATA *exit = get_exit( current.room, dir );
            if ( !exit || !exit->to_room || (!scanner && IS_SET( exit->exit_info, EX_SECRET | EX_HIDDEN )) )
                continue;

            const int exit_x = current.x + dx[dir];
            const int exit_y = current.y + dy[dir];
            const int room_x = exit_x + dx[dir];
            const int room_y = exit_y + dy[dir];
            if ( room_x < 0 || room_x >= map_width || room_y < 0 || room_y >= map_height )
                continue;

            ROOM_INDEX_DATA *destination = exit->to_room;
            ROOM_INDEX_DATA *&occupied = rooms[room_y][room_x];
            if ( (occupied && occupied != destination) || (!occupied && placed.count(destination)) )
                continue;

            if ( map[exit_y][exit_x] == ' ' )
                map[exit_y][exit_x] = connectors[dir];

            if ( occupied || (!scanner && IS_SET( exit->exit_info, EX_CLOSED )) )
                continue;

            occupied = destination;
            placed.insert( destination );
            pending.push( {destination, room_x, room_y, current.depth + 1} );
            const bool can_see = scanner || !room_is_dark( destination );
            map[room_y][room_x] = can_see ? 'O' : '?';
        }
    }

    std::string display = "+-----------+\n";
    for ( const auto &row : map )
        display += "|" + row + "|\n";
    display += "+-----------+\n";
    if ( scanner )
        send_to_pager( display, ch );
    else
        send_to_char( display, ch );
}

void show_room_map( CHAR_DATA *ch )
{
    if ( ch && ch->in_room )
        render_room_map( ch, false );
}

/* Clear, generate and display the map */
void do_draw( CHAR_DATA *ch, char *argument )
{
    (void)argument;
    OBJ_DATA *device;

    if ( !ch || !ch->in_room )
        return;

    if ( ( device = get_eq_char( ch, WEAR_HOLD ) ) == NULL )
    {
	send_to_char( "You must have a scanner to draw a map of the surrounding area.\n", ch );
	return;
    }

    if ( device->item_type != ITEM_DEVICE )
    {
	send_to_char( "You must have a scanner to draw a map of the surrounding area.\n", ch );
	return;
    }

    if (device->value[3] != 52 )
    {
	send_to_char( "You must have a scanner to draw a map of the surrounding area.\n", ch );
	return;
    }

    if ( device->value[2] <= 0 )
    {
        send_to_char( "Your scanner has no more charge left.\n", ch);
        return;
    }

    
    device->value[2]--;
    render_room_map( ch, true );
}

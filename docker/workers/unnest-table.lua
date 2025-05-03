local function rows(tbl)
    local r = pandoc.List()
    r:extend(tbl.head.rows)
    for _,b in ipairs(tbl.bodies) do r:extend(b.body) end
    r:extend(tbl.foot.rows)
    return r
  end
  
  function Table(t)
    local newHead = pandoc.TableHead()
    for i,row in ipairs(t.head.rows) do
      for j,cell in ipairs(row.cells) do
        local inner = cell.contents[1]
        if inner and inner.t == 'Table' then
          local ins = rows(inner)
          local first = table.remove(ins,1)
          row.cells[j] = first.cells[1]
          newHead.rows:insert(row)
          newHead.rows:extend(ins)
          goto continue
        end
      end
      newHead.rows:insert(row)
      ::continue::
    end
    t.head = newHead
    return t
  end
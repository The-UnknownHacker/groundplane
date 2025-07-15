document.addEventListener('DOMContentLoaded', function() {
    document.getElementById('log-search').addEventListener('input', filterAndSortLogs);
    document.getElementById('tag-filter').addEventListener('change', filterAndSortLogs);
    
    document.getElementById('sort-toggle').addEventListener('click', function() {
        sortOrder = sortOrder === 'desc' ? 'asc' : 'desc';
        this.innerHTML = `
            <i class='bx bx-sort-alt-2'></i>
            <span>${sortOrder === 'desc' ? 'Latest First' : 'Oldest First'}</span>
        `;
        filterAndSortLogs();
    });
});
</script>
{% endblock %}